"""
Departic controller: sets EVCC charging plans based on trip events.

Target logic:
  #trip + location -> route distance (round trip) + minSoc from EVCC -> SoC %
  #trip (no location) -> 100% SoC

Plan endpoint:
  POST /api/vehicles/{name}/plan/soc/{soc}/{timestamp}

EVCC clears the plan automatically when the charge-by time is reached
or the target is met. Departic removes the plan if the event is deleted.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

import requests
from dateutil import tz

from departic.models import (
    ActiveTripState,
    AppState,
    RouteCalculation,
    TripEvent,
    VehicleInfo,
)
from departic.notifier import NotifyEvent, notify
from departic.routing import calculate_trip_soc, enrich_calculation

if TYPE_CHECKING:
    from departic.config import Settings
    from departic.evcc import EvccAPI

log = logging.getLogger(__name__)

_REPLAN_THRESHOLD_PCT = 2


class TargetSource(StrEnum):
    """How the SoC target was determined."""

    DEFAULT = "default"  # no location set
    ROUTED = "routed"  # calculated via Nominatim + OSRM
    ROUTING_FAILED = "routing_failed"  # location set but routing failed
    CAPACITY_UNKNOWN = "capacity_unknown"  # EVCC didn't return capacity


class TripTarget(NamedTuple):
    """SoC target for a trip."""

    soc_pct: int
    source: TargetSource
    calculation: RouteCalculation | None  # raw calculation values for the first trip
    route_km: float | None  # one-way distance in km
    route_duration_min: float | None = None  # one-way driving duration in minutes

    # whether this target is the result of combining multiple back-to-back trips
    back_to_back: bool = False
    b2b_calculation: dict | None = None  # back-to-back combination values

    @property
    def label(self) -> str:
        """Human-readable label for the UI."""
        match self.source:
            case TargetSource.DEFAULT:
                return "100% (default)"
            case TargetSource.ROUTING_FAILED:
                return "100% (routing failed)"
            case TargetSource.CAPACITY_UNKNOWN:
                return "100% (capacity unknown)"
            case TargetSource.ROUTED:
                return ""  # caller uses location field directly
        return ""  # unreachable


def _get_vehicle(evcc: EvccAPI, cfg: Settings) -> VehicleInfo | None:
    try:
        return evcc.get_vehicle(cfg.evcc.vehicle_title)
    except requests.RequestException:
        log.exception("Could not read EVCC state")
    return None


def _clear_plan_if_needed(evcc: EvccAPI, state: AppState, cfg: Settings) -> AppState:
    if not state.active_trip_id:
        return state

    log.info("No upcoming trips but plan was active — deleting plan from EVCC.")
    vehicle = _get_vehicle(evcc, cfg)
    if vehicle:
        try:
            evcc.delete_plan_soc(vehicle.name)
        except requests.RequestException:
            log.exception("Failed to delete EVCC plan")

    notify(
        cfg.notifications,
        NotifyEvent.PLAN_CLEARED,
        summary=state.active_trip_target_label or state.active_trip_id,
    )

    return state.clear_plan()


def _resolve_target(
    event: TripEvent, vehicle: VehicleInfo, cfg: Settings
) -> TripTarget:
    """Resolve the SoC target for a single trip event."""
    if not event.location:
        return TripTarget(
            soc_pct=100, source=TargetSource.DEFAULT, calculation=None, route_km=None
        )

    if not cfg.evcc.home_address:
        log.warning(
            "Location set but home_address not configured — defaulting to 100%%."
        )
        return TripTarget(
            soc_pct=100, source=TargetSource.DEFAULT, calculation=None, route_km=None
        )

    if not vehicle.capacity_kwh:
        log.warning("Cannot calculate SoC: vehicle capacity unknown.")
        return TripTarget(
            soc_pct=100,
            source=TargetSource.CAPACITY_UNKNOWN,
            calculation=None,
            route_km=None,
        )

    min_soc = vehicle.min_soc_pct or 0

    trip_soc = calculate_trip_soc(
        location=event.location,
        home_address=cfg.evcc.home_address,
        capacity_kwh=vehicle.capacity_kwh,
        consumption_kwh_per_100km=cfg.vehicle.consumption_kwh_per_100km,
        duration_factor=cfg.vehicle.route_duration_factor,
    )

    if trip_soc is None:
        log.warning("Routing failed, falling back to 100%% SoC.")
        notify(
            cfg.notifications,
            NotifyEvent.ROUTING_FAILED,
            summary=event.summary,
            location=event.location,
        )
        return TripTarget(
            soc_pct=100,
            source=TargetSource.ROUTING_FAILED,
            calculation=None,
            route_km=None,
        )

    total_soc = min(100, trip_soc.soc_pct + min_soc)
    calculation = enrich_calculation(trip_soc.calculation, min_soc, total_soc)

    return TripTarget(
        soc_pct=total_soc,
        source=TargetSource.ROUTED,
        calculation=calculation,
        route_km=trip_soc.one_way_km,
        route_duration_min=trip_soc.duration_min,
    )


def _effective_target(
    upcoming: list[TripEvent],
    vehicle: VehicleInfo,
    cfg: Settings,
) -> TripTarget:
    """
    Determine the effective SoC target for the next trip.

    Collects all trips within the configured window of the first departure
    and sums their SoC requirements, accounting for the min_soc arrival
    buffer that is only needed once.
    """
    if not upcoming:
        return TripTarget(
            soc_pct=100, source=TargetSource.DEFAULT, calculation=None, route_km=None
        )

    first_event_time = upcoming[0].event_time
    window_hours = cfg.vehicle.back_to_back_window_hours
    window_end = first_event_time + timedelta(hours=window_hours)
    window_trips = [e for e in upcoming if e.event_time <= window_end]

    if len(window_trips) == 1:
        return _resolve_target(upcoming[0], vehicle, cfg)

    targets = [_resolve_target(e, vehicle, cfg) for e in window_trips]
    first = targets[0]
    min_soc = vehicle.min_soc_pct or 0
    # Each target already includes min_soc once; strip it to get raw route SoCs,
    # then add min_soc a single time for the combined total.
    raw_socs = [max(0, t.soc_pct - min_soc) for t in targets]
    total_soc = min(100, sum(raw_socs) + min_soc)

    if total_soc > first.soc_pct:
        log.info(
            (
                "Back-to-back trips within %.0fh — combined SoC: %s"
                " + %d%% min_soc = %d%% (first trip alone: %d%%)."
            ),
            window_hours,
            " + ".join(f"{s}%" for s in raw_socs),
            min_soc,
            total_soc,
            first.soc_pct,
        )
        b2b_trips = [
            {"soc_pct": s, "summary": e.summary}
            for s, e in zip(raw_socs, window_trips, strict=True)
        ]
        b2b_calculation = {
            "trips": b2b_trips,
            "min_soc_pct": min_soc,
            "total_soc_pct": total_soc,
        }
        return TripTarget(
            soc_pct=total_soc,
            source=first.source,
            calculation=first.calculation,
            route_km=first.route_km,
            route_duration_min=first.route_duration_min,
            back_to_back=True,
            b2b_calculation=b2b_calculation,
        )

    return first


def _plan_is_unchanged(state: AppState, trip_id: str, target_soc_pct: int) -> bool:
    return (
        trip_id == state.active_trip_id
        and state.active_trip_target_soc is not None
        and abs(state.active_trip_target_soc - target_soc_pct) < _REPLAN_THRESHOLD_PCT
    )


def _make_trip_state(
    trip_id: str, target: TripTarget, location: str
) -> ActiveTripState:
    """Build an ActiveTripState from a resolved target."""
    if target.source == TargetSource.ROUTING_FAILED and location:
        label = f"100% {location} (routing failed)"
    else:
        label = target.label or location
    return ActiveTripState(
        trip_id=trip_id,
        target_soc=target.soc_pct,
        target_label=label,
        calculation=target.calculation,
        b2b_calculation=target.b2b_calculation,
        route_km=target.route_km,
        route_duration_min=target.route_duration_min,
        back_to_back=target.back_to_back,
    )


def run_cycle(
    evcc: EvccAPI,
    events: list[TripEvent],
    state: AppState,
    cfg: Settings,
) -> AppState:
    """
    Process the next upcoming trip event and set the EVCC plan if needed.
    Returns updated AppState.
    """
    if not state.enabled:
        log.info("Departic is disabled — skipping plan update.")
        return state

    now = datetime.now(tz.tzlocal())
    upcoming = [e for e in events if e.event_time > now]

    if not upcoming:
        return _clear_plan_if_needed(evcc, state, cfg)

    vehicle = _get_vehicle(evcc, cfg)
    if not vehicle:
        log.warning("No vehicle found in EVCC, cannot set plan.")
        return state

    next_trip = upcoming[0]
    target = _effective_target(upcoming, vehicle, cfg)

    # Charge must be complete by departure time (event start minus driving duration).
    if target.route_duration_min is not None:
        deadline = next_trip.event_time - timedelta(minutes=target.route_duration_min)
    else:
        deadline = next_trip.event_time

    if _plan_is_unchanged(state, next_trip.trip_id, target.soc_pct):
        try:
            plan_exists = evcc.has_plan_soc(vehicle.name)
        except requests.RequestException:
            log.exception("Failed to verify EVCC plan status, assume it exists.")
            plan_exists = True

        if plan_exists:
            log.debug(
                "Plan unchanged (%s, %d%%), skipping.",
                deadline.strftime("%d-%m %H:%M"),
                target.soc_pct,
            )
            return state.model_copy(
                update={
                    "active_trip": _make_trip_state(
                        next_trip.trip_id, target, next_trip.location
                    )
                }
            )
        log.info("Plan was removed in EVCC — reapplying.")
        reapply = True
    else:
        reapply = False

    try:
        evcc.set_plan_soc(vehicle.name, target.soc_pct, deadline)
    except requests.RequestException:
        log.exception("Failed to set EVCC plan")
        return state

    log.info(
        "Plan set: '%s' | %s | %d%% SoC (vehicle: %s)",
        next_trip.summary,
        deadline.strftime("%d-%m %H:%M"),
        target.soc_pct,
        vehicle.name,
    )

    # ── Notify (skip silent re-applies) ───────────────────────────────
    if not reapply:
        old_soc = state.active_trip_target_soc
        if old_soc is not None and state.active_trip_id:
            notify(
                cfg.notifications,
                NotifyEvent.PLAN_UPDATED,
                summary=next_trip.summary,
                old_soc_pct=old_soc,
                new_soc_pct=target.soc_pct,
            )
        else:
            notify(
                cfg.notifications,
                NotifyEvent.PLAN_ACTIVATED,
                summary=next_trip.summary,
                soc_pct=target.soc_pct,
                deadline=deadline.strftime("%a %d-%m %H:%M"),
                route_km=target.route_km,
            )

    return state.model_copy(
        update={
            "active_trip": _make_trip_state(
                next_trip.trip_id, target, next_trip.location
            )
        }
    )
