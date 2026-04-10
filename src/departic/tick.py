"""
Departic tick — one scheduler cycle.
"""

from __future__ import annotations

import logging
from datetime import datetime

import requests
from dateutil import tz

from departic import __version__
from departic import state as state_store
from departic.config import CONFIG_PATH, Settings
from departic.controller import run_cycle
from departic.evcc import EvccAPI
from departic.ical import parse_events
from departic.models import AppState, LiveStatus, TripEvent
from departic.status_builder import (
    back_to_back_trip_ids,
    build_active_plan,
    build_upcoming_trips,
    precalculate_labels,
)

log = logging.getLogger(__name__)


def _load_events(cfg: Settings) -> tuple[list[TripEvent], str | None]:
    """Fetch all calendar feeds. Returns (events, error_string | None)."""
    try:
        events, feed_errors = parse_events(cfg)
    except Exception:
        log.exception("Failed to fetch calendars")
        return [], "Calendar unavailable"

    ical_error = "; ".join(feed_errors) if feed_errors else None
    return events, ical_error


def _run_controller(
    cfg: Settings,
    events: list[TripEvent],
    evcc: EvccAPI,
) -> AppState:
    """Run one controller cycle. Saves state on success."""
    current_state = state_store.load()
    try:
        new_state = run_cycle(evcc, events, current_state, cfg)
    except Exception:
        log.exception("Controller error")
        return current_state
    state_store.save(new_state)
    return new_state


# Module-level EVCC session — created once, reused across ticks
_evcc: EvccAPI | None = None


def _get_evcc(url: str) -> EvccAPI:
    """Return the module-level EvccAPI session, creating it if needed."""
    global _evcc  # noqa: PLW0603
    if _evcc is None or _evcc.base != url.rstrip("/") + "/api":
        _evcc = EvccAPI(url)
    return _evcc


def run_tick(evcc: EvccAPI | None = None) -> LiveStatus:
    """
    Execute one full Departic cycle and return the resulting LiveStatus.

    Accepts an EvccAPI instance for injection in tests.
    """
    cfg = Settings.reload()
    if cfg is None:
        return LiveStatus(
            version=__version__,
            tick_error=f"Configuration error — check {CONFIG_PATH}.",
        )

    if not cfg.is_configured():
        return LiveStatus(
            version=__version__,
            tick_error=f"Configuration incomplete — edit {CONFIG_PATH}.",
        )

    api = evcc if evcc is not None else _get_evcc(cfg.evcc.url)
    events, ical_error = _load_events(cfg)
    new_state = _run_controller(cfg, events, api)

    now = datetime.now(tz.tzlocal())
    future_events = [e for e in events if e.event_time > now]

    # Fetch vehicle once — reused for pre-calculation
    try:
        vehicle_info = api.get_vehicle(cfg.evcc.vehicle_title)
    except requests.RequestException:
        log.warning("Could not fetch vehicle info for pre-calculation.")
        vehicle_info = None

    # Fetch 30-day average energy price for trip cost estimation
    try:
        avg_price = api.get_avg_price()
    except requests.RequestException:
        log.warning("Could not fetch average price from EVCC.")
        avg_price = None

    # Fetch live loadpoint / charging status
    evcc_status = api.get_loadpoint_status(cfg.evcc.vehicle_title)

    pre_results = precalculate_labels(
        events=future_events,
        cfg=cfg,
        active_trip_id=new_state.active_trip_id,
        resolved_label=new_state.active_trip_target_label,
        active_soc=new_state.active_trip_target_soc,
        active_route_km=new_state.active_trip_route_km,
        active_route_duration_min=new_state.active_trip_route_duration_min,
        vehicle=vehicle_info,
        avg_price=avg_price,
    )

    b2b_ids = back_to_back_trip_ids(future_events, new_state, cfg)
    upcoming = build_upcoming_trips(events, now, new_state, pre_results, b2b_ids)
    active_plan = build_active_plan(events, new_state, avg_price=avg_price)

    log.info(
        "Cycle complete. %d upcoming trip(s), %d total events.",
        len(upcoming),
        len(events),
    )

    return LiveStatus(
        version=__version__,
        enabled=new_state.enabled,
        avg_price_eur=avg_price,
        upcoming_trips=upcoming,
        active_plan=active_plan,
        evcc_status=evcc_status,
        tick_updated=now.strftime("%H:%M:%S"),
        tick_error=ical_error,
    )
