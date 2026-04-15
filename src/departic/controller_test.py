"""
Tests for the Departic controller.
"""

from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import quote

import pytest
import responses as rsps_lib
from dateutil import tz

from departic.cache import GeocodeCache, RouteCache, geocode_cache, route_cache
from departic.config import AgendaConfig, EvccConfig, Settings, VehicleConfig
from departic.controller import (
    TargetSource,
    TripTarget,
    _clear_plan_if_needed,
    _effective_target,
    _get_vehicle,
    _make_trip_state,
    _resolve_target,
    run_cycle,
)
from departic.evcc import EvccAPI
from departic.models import (
    ActiveTripState,
    AppState,
    RouteCalculation,
    TripEvent,
    VehicleInfo,
)
from departic.routing import calculate_trip_soc


def plan_post_url(evcc_url: str, vehicle: str, soc: int, ts: datetime) -> str:
    """Build the expected POST URL for a plan/soc call, matching encoding."""
    return (
        f"{evcc_url}/api/vehicles/{quote(vehicle, safe=':')}"
        f"/plan/soc/{soc}/{quote(ts.isoformat(), safe='-:.T')}"
    )


@pytest.fixture(autouse=True)
def _clear_global_caches():
    """Ensure module-level caches are clean before and after every test."""
    geocode_cache.clear()
    route_cache.clear()
    yield
    geocode_cache.clear()
    route_cache.clear()


def make_evcc(evcc_url) -> EvccAPI:
    return EvccAPI(evcc_url)


def make_event(
    hours_from_now: float = 24, location: str = "", summary: str = "Test trip"
) -> TripEvent:
    event_time = datetime.now(tz.tzlocal()) + timedelta(hours=hours_from_now)
    return TripEvent(
        summary=summary, event_time=event_time, location=location, feed_name="Test"
    )


def make_vehicle(**kwargs) -> VehicleInfo:
    defaults = {
        "name": "mycar",
        "title": "MyCar",
        "capacity_kwh": 64.0,
        "min_soc_pct": 20,
    }
    return VehicleInfo(**(defaults | kwargs))


# ── TripTarget.label ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "soc_pct", "expected_label"),
    [
        (TargetSource.DEFAULT, 100, "100% (default)"),
        (TargetSource.ROUTING_FAILED, 100, "100% (routing failed)"),
        (TargetSource.CAPACITY_UNKNOWN, 100, "100% (capacity unknown)"),
        # ROUTED: label is intentionally empty; the UI uses the location field directly.
        (TargetSource.ROUTED, 42, ""),
    ],
)
def test_trip_target_label(source, soc_pct, expected_label):
    t = TripTarget(soc_pct=soc_pct, source=source, calculation={}, route_km=70.0)
    assert t.label == expected_label


# ── _get_vehicle ──────────────────────────────────────────────────────────


@rsps_lib.activate
def test_get_vehicle_request_error(evcc_url, cfg):
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", body=rsps_lib.ConnectionError())
    result = _get_vehicle(make_evcc(evcc_url), cfg)
    assert result is None


@rsps_lib.activate
def test_get_vehicle_value_error(evcc_url, cfg):
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", body="not json")
    result = _get_vehicle(make_evcc(evcc_url), cfg)
    assert result is None


# ── _clear_plan_if_needed ─────────────────────────────────────────────────


def test_clear_plan_no_active_trip(evcc_url, cfg):
    state = AppState()
    result = _clear_plan_if_needed(make_evcc(evcc_url), state, cfg)
    assert result.active_trip is None


@rsps_lib.activate
def test_clear_plan_deletes_from_evcc(evcc_url, evcc_state, cfg):
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=evcc_state)
    rsps_lib.add(
        rsps_lib.DELETE,
        f"{evcc_url}/api/vehicles/mycar/plan/soc",
        json={"result": {}},
    )
    state = AppState(active_trip=ActiveTripState(trip_id="old", target_soc=80))
    result = _clear_plan_if_needed(make_evcc(evcc_url), state, cfg)
    assert result.active_trip is None
    assert len([c for c in rsps_lib.calls if c.request.method == "DELETE"]) == 1


@rsps_lib.activate
def test_clear_plan_delete_failure_still_clears(evcc_url, evcc_state, cfg):
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=evcc_state)
    rsps_lib.add(rsps_lib.DELETE, f"{evcc_url}/api/vehicles/mycar/plan/soc", status=500)
    state = AppState(active_trip=ActiveTripState(trip_id="old", target_soc=80))
    result = _clear_plan_if_needed(make_evcc(evcc_url), state, cfg)
    assert result.active_trip is None


# ── _resolve_target ───────────────────────────────────────────────────────


def test_resolve_target_no_location(cfg):
    target = _resolve_target(make_event(location=""), make_vehicle(), cfg)
    assert target.source == TargetSource.DEFAULT
    assert target.soc_pct == 100


def test_resolve_target_no_home_address():
    cfg = Settings(
        evcc=EvccConfig(url="http://evcc.test", vehicle_title="MyCar", home_address=""),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(feeds=[]),
    )
    target = _resolve_target(make_event(location="Gotham"), make_vehicle(), cfg)
    assert target.source == TargetSource.DEFAULT
    assert target.soc_pct == 100


def test_resolve_target_no_capacity(cfg):
    target = _resolve_target(
        make_event(location="Gotham"), make_vehicle(capacity_kwh=None), cfg
    )
    assert target.source == TargetSource.CAPACITY_UNKNOWN


def test_resolve_target_routing_fails(cfg):
    """When routing returns None, should fall back to 100%."""
    with patch("departic.controller.calculate_trip_soc", return_value=None):
        target = _resolve_target(make_event(location="Nowhere"), make_vehicle(), cfg)
    assert target.source == TargetSource.ROUTING_FAILED
    assert target.soc_pct == 100


def test_resolve_target_routed(cfg):
    geocode_cache.set("Smallville, Midwest, Freedonia", (10.123, 20.456))
    geocode_cache.set("Gotham", (11.234, 21.567))
    route_cache.set("Smallville, Midwest, Freedonia", "Gotham", 35.0, 1800.0)

    target = _resolve_target(make_event(location="Gotham"), make_vehicle(), cfg)
    assert target.source == TargetSource.ROUTED
    assert target.soc_pct == 42
    assert target.route_km == 35.0
    assert target.route_duration_min == 30.0


# ── _effective_target ─────────────────────────────────────────────────────


def test_effective_target_empty_list(cfg):
    target = _effective_target([], make_vehicle(), cfg)
    assert target.soc_pct == 100
    assert target.source == TargetSource.DEFAULT


def test_effective_target_single_trip(cfg):
    target = _effective_target([make_event()], make_vehicle(), cfg)
    assert target.soc_pct == 100


def test_effective_target_back_to_back(cfg):
    """Two trips within window → combined SoC.

    Trip A: 50 km x 2 = 100 km → round(100*20/100/64*100) = 31% raw
    Trip B: 60 km x 2 = 120 km → round(120*20/100/64*100) = 38% raw
    Combined: min(100, 31 + 38 + 20% min_soc) = 89%
    """
    geocode_cache.set("Smallville, Midwest, Freedonia", (10.123, 20.456))
    geocode_cache.set("A", (53.0, 6.6))
    geocode_cache.set("B", (53.1, 6.7))
    route_cache.set("Smallville, Midwest, Freedonia", "A", 50.0, 2400.0)
    route_cache.set("Smallville, Midwest, Freedonia", "B", 60.0, 3000.0)

    e1 = make_event(hours_from_now=5, location="A")
    e2 = make_event(hours_from_now=10, location="B")

    target = _effective_target([e1, e2], make_vehicle(), cfg)
    assert target.soc_pct == 89
    assert target.back_to_back is True


# ── _make_trip_state ──────────────────────────────────────────────────────


def test_make_trip_state():
    calc = RouteCalculation(
        one_way_km=35.0,
        round_trip_km=70.0,
        consumption=20.0,
        round_trip_kwh=14.0,
        capacity_kwh=64.0,
        route_soc_pct=22,
        min_soc_pct=20,
        total_soc_pct=42,
    )
    target = TripTarget(
        soc_pct=42,
        source=TargetSource.ROUTED,
        calculation=calc,
        route_km=70.0,
        route_duration_min=30.0,
    )
    state = _make_trip_state("trip-1", target, "Gotham")
    assert state.trip_id == "trip-1"
    assert state.target_soc == 42
    assert state.target_label == "Gotham"
    assert state.calculation == calc
    assert state.route_km == 70.0
    assert state.route_duration_min == 30.0


def test_make_trip_state_routing_failed():
    target = TripTarget(
        soc_pct=100, source=TargetSource.ROUTING_FAILED, calculation=None, route_km=None
    )
    state = _make_trip_state("trip-1", target, "Gotham")
    assert state.target_label == "100% Gotham (routing failed)"


# ── run_cycle ─────────────────────────────────────────────────────────────


@rsps_lib.activate
def test_plan_set_on_first_cycle(evcc_url, evcc_state, cfg):
    event = make_event(hours_from_now=24)
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=evcc_state)
    rsps_lib.add(
        rsps_lib.POST,
        plan_post_url(evcc_url, "mycar", 100, event.event_time),
        json={"result": {}},
    )
    new_state = run_cycle(make_evcc(evcc_url), [event], AppState(), cfg)
    assert new_state.active_trip_id == event.trip_id
    assert new_state.active_trip_target_soc == 100


@rsps_lib.activate
def test_plan_uses_event_time_as_deadline_when_routed(evcc_url, evcc_state, cfg):
    """When route duration is known,
    EVCC plan deadline = event time minus driving duration (departure time)."""
    geocode_cache.set("Smallville, Midwest, Freedonia", (10.123, 20.456))
    geocode_cache.set("Gotham", (11.234, 21.567))
    route_cache.set("Smallville, Midwest, Freedonia", "Gotham", 35.0, 1800.0)

    event = make_event(hours_from_now=24, location="Gotham")
    # Route duration is 1800 s = 30 min → departure time = event_time - 30 min
    departure_time = event.event_time - timedelta(minutes=30)

    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=evcc_state)
    rsps_lib.add(
        rsps_lib.POST,
        plan_post_url(evcc_url, "mycar", 42, departure_time),
        json={"result": {}},
    )
    new_state = run_cycle(make_evcc(evcc_url), [event], AppState(), cfg)
    assert new_state.active_trip_id == event.trip_id
    assert new_state.active_trip_target_soc == 42
    # Verify the POST was made with departure_time (event_time - drive_duration)
    # as the deadline
    post_calls = [c for c in rsps_lib.calls if c.request.method == "POST"]
    assert len(post_calls) == 1
    assert quote(departure_time.isoformat(), safe="-:.T") in post_calls[0].request.url


@rsps_lib.activate
def test_plan_skipped_if_unchanged(evcc_url, evcc_state, cfg):
    event = make_event(hours_from_now=24)
    state = AppState(active_trip=ActiveTripState(trip_id=event.trip_id, target_soc=100))
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=evcc_state)
    rsps_lib.add(
        rsps_lib.GET,
        f"{evcc_url}/api/vehicles/mycar/plan/soc",
        json={"result": {"soc": 100, "time": "2026-04-05T08:00:00Z"}},
    )
    run_cycle(make_evcc(evcc_url), [event], state, cfg)
    post_calls = [c for c in rsps_lib.calls if c.request.method == "POST"]
    assert len(post_calls) == 0


@rsps_lib.activate
def test_plan_reapplied_if_removed_in_evcc(evcc_url, evcc_state, cfg):
    event = make_event(hours_from_now=24)
    state = AppState(active_trip=ActiveTripState(trip_id=event.trip_id, target_soc=100))
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=evcc_state)
    rsps_lib.add(
        rsps_lib.GET,
        f"{evcc_url}/api/vehicles/mycar/plan/soc",
        json={"result": {"soc": 0, "time": "0001-01-01T00:00:00Z"}},
    )
    rsps_lib.add(
        rsps_lib.POST,
        plan_post_url(evcc_url, "mycar", 100, event.event_time),
        json={"result": {}},
    )
    run_cycle(make_evcc(evcc_url), [event], state, cfg)
    post_calls = [c for c in rsps_lib.calls if c.request.method == "POST"]
    assert len(post_calls) == 1


@rsps_lib.activate
def test_reapply_does_not_notify(evcc_url, evcc_state, cfg):
    """When EVCC removed the plan (target reached) and Departic re-applies it,
    no notification should be sent — this is a silent re-apply."""
    event = make_event(hours_from_now=24)
    state = AppState(active_trip=ActiveTripState(trip_id=event.trip_id, target_soc=100))
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=evcc_state)
    rsps_lib.add(
        rsps_lib.GET,
        f"{evcc_url}/api/vehicles/mycar/plan/soc",
        json={"result": {"soc": 0, "time": "0001-01-01T00:00:00Z"}},
    )
    rsps_lib.add(
        rsps_lib.POST,
        plan_post_url(evcc_url, "mycar", 100, event.event_time),
        json={"result": {}},
    )
    with patch("departic.controller.notify") as mock_notify:
        run_cycle(make_evcc(evcc_url), [event], state, cfg)
        mock_notify.assert_not_called()


@rsps_lib.activate
def test_new_plan_does_notify(evcc_url, evcc_state, cfg):
    """A genuinely new plan (no prior active trip) should send a notification."""
    event = make_event(hours_from_now=24)
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=evcc_state)
    rsps_lib.add(
        rsps_lib.POST,
        plan_post_url(evcc_url, "mycar", 100, event.event_time),
        json={"result": {}},
    )
    with patch("departic.controller.notify") as mock_notify:
        run_cycle(make_evcc(evcc_url), [event], AppState(), cfg)
        mock_notify.assert_called_once()


@rsps_lib.activate
def test_no_plan_when_disabled(evcc_url, cfg):
    run_cycle(make_evcc(evcc_url), [make_event()], AppState(enabled=False), cfg)
    assert len(rsps_lib.calls) == 0


@rsps_lib.activate
def test_plan_deleted_when_no_upcoming_trips(evcc_url, cfg):
    rsps_lib.add(
        rsps_lib.GET,
        f"{evcc_url}/api/state",
        json={
            "vehicles": {"mycar": {"title": "MyCar", "capacity": 64.0, "minSoc": 20}},
            "loadpoints": [],
        },
    )
    rsps_lib.add(
        rsps_lib.DELETE,
        f"{evcc_url}/api/vehicles/mycar/plan/soc",
        json={"result": {}},
    )
    state = AppState(
        active_trip=ActiveTripState(trip_id="some-past-trip", target_soc=80)
    )
    new_state = run_cycle(make_evcc(evcc_url), [], state, cfg)
    assert new_state.active_trip_id is None


@rsps_lib.activate
def test_run_cycle_no_vehicle_found(evcc_url, cfg):
    """When vehicle is not found in EVCC, return unchanged state."""
    rsps_lib.add(
        rsps_lib.GET,
        f"{evcc_url}/api/state",
        json={
            "vehicles": {},
            "loadpoints": [],
        },
    )
    event = make_event(hours_from_now=24)
    state = run_cycle(make_evcc(evcc_url), [event], AppState(), cfg)
    assert state.active_trip is None


@rsps_lib.activate
def test_run_cycle_set_plan_fails(evcc_url, evcc_state, cfg):
    """When the POST to set the plan fails, return unchanged state."""
    event = make_event(hours_from_now=24)
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=evcc_state)
    rsps_lib.add(
        rsps_lib.POST,
        plan_post_url(evcc_url, "mycar", 100, event.event_time),
        status=500,
    )
    state = run_cycle(make_evcc(evcc_url), [event], AppState(), cfg)
    assert state.active_trip is None


@rsps_lib.activate
def test_run_cycle_has_plan_check_fails(evcc_url, evcc_state, cfg):
    """When has_plan_soc fails, assume plan exists and skip reapply."""
    event = make_event(hours_from_now=24)
    state = AppState(active_trip=ActiveTripState(trip_id=event.trip_id, target_soc=100))
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=evcc_state)
    rsps_lib.add(
        rsps_lib.GET,
        f"{evcc_url}/api/vehicles/mycar/plan/soc",
        body=rsps_lib.ConnectionError(),
    )
    result = run_cycle(make_evcc(evcc_url), [event], state, cfg)
    # Should not crash; assumes plan exists and skips
    assert result.active_trip_id == event.trip_id
    post_calls = [c for c in rsps_lib.calls if c.request.method == "POST"]
    assert len(post_calls) == 0


# ── calculate_trip_soc ────────────────────────────────────────────────────


def test_calculate_trip_soc(cfg):
    gc = GeocodeCache()
    rc = RouteCache()
    gc.set("Smallville, Midwest, Freedonia", (10.123, 20.456))
    gc.set("Gotham", (11.234, 21.567))
    rc.set("Smallville, Midwest, Freedonia", "Gotham", 35.0, 1800.0)

    soc, calculation, one_way_km, duration_min = calculate_trip_soc(
        location="Gotham",
        home_address="Smallville, Midwest, Freedonia",
        capacity_kwh=64.0,
        consumption_kwh_per_100km=20.0,
        route_cache=rc,
        geocode_cache=gc,
    )
    assert soc == 22
    assert one_way_km == 35.0
    assert duration_min == 30.0
    assert calculation.round_trip_km == 70.0
