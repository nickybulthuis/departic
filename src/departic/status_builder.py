"""
Departic status builder.

Transforms domain objects (TripEvent, AppState) into UI-ready models
(LiveStatus, UpcomingTrip, ActivePlan) that are served via GET /api/status.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from departic.controller import TargetSource
from departic.models import (
    ActivePlan,
    AppState,
    RouteCalculation,
    TripEvent,
    UpcomingTrip,
    VehicleInfo,
)
from departic.routing import calculate_trip_soc, enrich_calculation

if TYPE_CHECKING:
    from departic.config import Settings

log = logging.getLogger(__name__)


class LabelResult(NamedTuple):
    """Pre-calculated SoC target for a single upcoming trip."""

    label: str
    soc_pct: int | None
    route_km: float | None
    source: TargetSource
    calculation: RouteCalculation | None
    duration_min: float | None


def _trip_cost(round_trip_kwh: float | None, avg_price: float | None) -> float | None:
    """Compute estimated trip cost in EUR, or None if data is missing."""
    if round_trip_kwh and avg_price:
        return round(round_trip_kwh * avg_price, 2)
    return None


def precalculate_labels(
    events: list[TripEvent],
    cfg: Settings,
    active_trip_id: str | None,
    resolved_label: str | None,
    active_soc: int | None,
    active_route_km: float | None,
    active_route_duration_min: float | None = None,
    vehicle: VehicleInfo | None = None,
    avg_price: float | None = None,
) -> dict[str, LabelResult]:
    """
    Pre-calculate SoC targets for all upcoming events with a location.

    Returns {trip_id: LabelResult(...)}.

    Accepts VehicleInfo directly so it can be injected in tests without
    requiring a live EVCC instance.
    """
    results: dict[str, LabelResult] = {}

    if not cfg.evcc.home_address:
        return results

    capacity_kwh = vehicle.capacity_kwh if vehicle else None
    min_soc_pct = (vehicle.min_soc_pct or 0) if vehicle else 0

    for event in events:
        trip_id = event.trip_id

        if trip_id == active_trip_id and resolved_label:
            results[trip_id] = LabelResult(
                label=resolved_label,
                soc_pct=active_soc,
                route_km=active_route_km,
                source=TargetSource.ROUTED,
                calculation=None,  # calculation shown via active plan card
                duration_min=active_route_duration_min,
            )
            continue

        if not event.location or not capacity_kwh:
            continue

        trip_soc = calculate_trip_soc(
            location=event.location,
            home_address=cfg.evcc.home_address,
            capacity_kwh=capacity_kwh,
            consumption_kwh_per_100km=cfg.vehicle.consumption_kwh_per_100km,
            duration_factor=cfg.vehicle.route_duration_factor,
        )

        if trip_soc is not None:
            total_soc = min(100, trip_soc.soc_pct + min_soc_pct)
            calculation = enrich_calculation(
                trip_soc.calculation, min_soc_pct, total_soc
            )
            cost = _trip_cost(calculation.round_trip_kwh, avg_price)
            if cost is not None:
                calculation = calculation.model_copy(update={"trip_cost_eur": cost})
            results[trip_id] = LabelResult(
                label=event.location,
                soc_pct=total_soc,
                route_km=trip_soc.one_way_km,
                source=TargetSource.ROUTED,
                calculation=calculation,
                duration_min=trip_soc.duration_min,
            )
        else:
            results[trip_id] = LabelResult(
                label=event.location,
                soc_pct=100,
                route_km=None,
                source=TargetSource.ROUTING_FAILED,
                calculation=None,
                duration_min=None,
            )

    return results


def build_upcoming_trips(
    events: list[TripEvent],
    now: datetime,
    state: AppState,
    pre_results: dict[str, LabelResult],
    back_to_back_ids: set[str] | None = None,
) -> list[UpcomingTrip]:
    """Build the UI trip list from parsed events and pre-calculated labels."""
    upcoming: list[UpcomingTrip] = []

    for event in events:
        if event.event_time <= now:
            continue

        trip_id = event.trip_id

        # The active trip is already shown in the active plan card — skip it here.
        if trip_id == state.active_trip_id:
            continue

        if trip_id in pre_results:
            result = pre_results[trip_id]
        elif event.location:
            result = LabelResult(
                label="100% (routing failed)",
                soc_pct=100,
                route_km=None,
                source=TargetSource.ROUTING_FAILED,
                calculation=None,
                duration_min=None,
            )
        else:
            result = LabelResult(
                label="100% (default)",
                soc_pct=100,
                route_km=None,
                source=TargetSource.DEFAULT,
                calculation=None,
                duration_min=None,
            )

        is_back_to_back = bool(back_to_back_ids and trip_id in back_to_back_ids)
        if result.source == TargetSource.ROUTED and result.route_km and result.label:
            display_label = f"{result.label} · {round(result.route_km)} km"
        elif result.source == TargetSource.ROUTING_FAILED:
            loc = event.location or result.label
            display_label = (
                f"100% {loc} (routing failed)" if loc else "100% (routing failed)"
            )
        elif result.source == TargetSource.DEFAULT:
            display_label = "100% (default)"
        else:
            display_label = result.label
        upcoming.append(
            UpcomingTrip(
                summary=event.summary,
                event_time=event.event_time.isoformat(),
                feed_name=event.feed_name,
                location=event.location,
                target_label=display_label,
                target_soc_pct=result.soc_pct,
                route_km=result.route_km,
                route_duration_min=result.duration_min,
                soc_source=result.source,
                calculation=result.calculation,
                back_to_back=is_back_to_back,
                trip_cost_eur=(
                    result.calculation.trip_cost_eur if result.calculation else None
                ),
            )
        )

    return upcoming


def build_active_plan(
    events: list[TripEvent],
    state: AppState,
    avg_price: float | None = None,
) -> ActivePlan | None:
    """Build the active plan card model from the current state."""
    active_event = next((e for e in events if e.trip_id == state.active_trip_id), None)
    if active_event is None:
        return None

    if state.active_trip_route_km:
        soc_source = TargetSource.ROUTED
    elif active_event.location:
        soc_source = TargetSource.ROUTING_FAILED
    else:
        soc_source = TargetSource.DEFAULT

    calc = state.active_trip_calculation
    cost = _trip_cost(calc.round_trip_kwh if calc else None, avg_price)
    if cost is not None and calc is not None:
        calc = calc.model_copy(update={"trip_cost_eur": cost})

    return ActivePlan(
        summary=active_event.summary,
        event_time=active_event.event_time.isoformat(),
        feed_name=active_event.feed_name,
        location=active_event.location,
        target_soc_pct=state.active_trip_target_soc,
        route_km=state.active_trip_route_km,
        route_duration_min=state.active_trip_route_duration_min,
        calculation=calc,
        b2b_calculation=state.active_trip_b2b_calculation,
        soc_source=soc_source,
        back_to_back=state.active_trip_back_to_back,
        trip_cost_eur=cost,
    )


def back_to_back_trip_ids(
    future_events: list[TripEvent],
    state: AppState,
    cfg: Settings,
) -> set[str]:
    """Return trip_ids of all events in the back-to-back window."""
    if not state.active_trip_back_to_back or not future_events:
        return set()

    first_dep = future_events[0].event_time
    window_end = first_dep + timedelta(hours=cfg.vehicle.back_to_back_window_hours)
    return {e.trip_id for e in future_events if e.event_time <= window_end}
