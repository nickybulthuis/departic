"""
Tests for status_builder — UI model construction from domain objects.
All tests are pure: no HTTP calls, no EVCC, no APScheduler.
"""

from datetime import datetime, timedelta

import pytest
from dateutil import tz

from departic.cache import geocode_cache, route_cache
from departic.config import AgendaConfig, EvccConfig, Settings, VehicleConfig
from departic.controller import TargetSource
from departic.models import (
    ActiveTripState,
    AppState,
    Coords,
    RouteCalculation,
    TripEvent,
    VehicleInfo,
)
from departic.status_builder import (
    LabelResult,
    back_to_back_trip_ids,
    build_active_plan,
    build_upcoming_trips,
    precalculate_labels,
)


@pytest.fixture(autouse=True)
def _clear_global_caches():
    geocode_cache.clear()
    route_cache.clear()
    yield
    geocode_cache.clear()
    route_cache.clear()


@pytest.fixture
def cfg():
    return Settings(
        evcc=EvccConfig(
            url="http://evcc.test",
            vehicle_title="bZ4X",
            home_address="Smallville, Midwest, Freedonia",
        ),
        vehicle=VehicleConfig(consumption_kwh_per_100km=20.0),
        agenda=AgendaConfig(feeds=[]),
    )


@pytest.fixture
def vehicle():
    return VehicleInfo(
        name="bz4x",
        title="bZ4X",
        capacity_kwh=64.0,
        min_soc_pct=20,
    )


def make_event(hours: float = 24, location: str = "Gotham") -> TripEvent:
    event_time = datetime.now(tz.tzlocal()) + timedelta(hours=hours)
    return TripEvent(
        summary="Trip", event_time=event_time, location=location, feed_name="Cal"
    )


# ── precalculate_labels ───────────────────────────────────────────────────


def test_precalculate_labels_routed(cfg, vehicle):
    geocode_cache.set("Smallville, Midwest, Freedonia", Coords(10.123, 20.456))
    geocode_cache.set("Gotham", Coords(11.234, 21.567))
    route_cache.set("Smallville, Midwest, Freedonia", "Gotham", 35.0, 1800.0)

    event = make_event(location="Gotham")
    results = precalculate_labels(
        events=[event],
        cfg=cfg,
        active_trip_id=None,
        resolved_label=None,
        active_soc=None,
        active_route_km=None,
        vehicle=vehicle,
    )

    assert event.trip_id in results
    label, soc, km, source, calculation, duration_min = results[event.trip_id]
    assert label == "Gotham"
    assert source == TargetSource.ROUTED
    assert soc == 42
    assert km == 35.0
    assert duration_min == 30.0
    assert calculation is not None


def test_precalculate_labels_routed_with_cost(cfg, vehicle):
    geocode_cache.set("Smallville, Midwest, Freedonia", Coords(10.123, 20.456))
    geocode_cache.set("Gotham", Coords(11.234, 21.567))
    route_cache.set("Smallville, Midwest, Freedonia", "Gotham", 35.0, 1800.0)

    event = make_event(location="Gotham")
    results = precalculate_labels(
        events=[event],
        cfg=cfg,
        active_trip_id=None,
        resolved_label=None,
        active_soc=None,
        active_route_km=None,
        vehicle=vehicle,
        avg_price=0.25,
    )

    _, _, _, _, calculation, _ = results[event.trip_id]
    assert calculation is not None
    assert calculation.trip_cost_eur == round(14.0 * 0.25, 2)


def test_precalculate_labels_routed_without_cost(cfg, vehicle):
    geocode_cache.set("Smallville, Midwest, Freedonia", Coords(10.123, 20.456))
    geocode_cache.set("Gotham", Coords(11.234, 21.567))
    route_cache.set("Smallville, Midwest, Freedonia", "Gotham", 35.0, 1800.0)

    event = make_event(location="Gotham")
    results = precalculate_labels(
        events=[event],
        cfg=cfg,
        active_trip_id=None,
        resolved_label=None,
        active_soc=None,
        active_route_km=None,
        vehicle=vehicle,
        avg_price=None,
    )

    _, _, _, _, calculation, _ = results[event.trip_id]
    assert calculation is not None
    assert calculation.trip_cost_eur is None


def test_precalculate_labels_no_vehicle(cfg):
    event = make_event(location="Gotham")
    results = precalculate_labels(
        events=[event],
        cfg=cfg,
        active_trip_id=None,
        resolved_label=None,
        active_soc=None,
        active_route_km=None,
        vehicle=None,
    )
    assert event.trip_id not in results


def test_precalculate_labels_no_home_address(vehicle):
    cfg = Settings(
        evcc=EvccConfig(url="http://evcc.test", vehicle_title="bZ4X", home_address=""),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(feeds=[]),
    )
    event = make_event(location="Gotham")
    results = precalculate_labels(
        events=[event],
        cfg=cfg,
        active_trip_id=None,
        resolved_label=None,
        active_soc=None,
        active_route_km=None,
        vehicle=vehicle,
    )
    assert results == {}


def test_precalculate_labels_uses_resolved_for_active(cfg, vehicle):
    event = make_event(location="Gotham")
    results = precalculate_labels(
        events=[event],
        cfg=cfg,
        active_trip_id=event.trip_id,
        resolved_label="My label",
        active_soc=55,
        active_route_km=70.0,
        active_route_duration_min=30.0,
        vehicle=vehicle,
    )
    label, soc, km, source, calculation, duration_min = results[event.trip_id]
    assert label == "My label"
    assert soc == 55
    assert km == 70.0
    assert duration_min == 30.0
    assert source == TargetSource.ROUTED
    assert calculation is None


# ── build_upcoming_trips ──────────────────────────────────────────────────


def test_build_upcoming_trips_basic(cfg):
    now = datetime.now(tz.tzlocal())
    event = make_event(hours=24)
    state = AppState()
    result = build_upcoming_trips([event], now, state, {})
    assert len(result) == 1
    assert result[0].summary == event.summary


def test_build_upcoming_trips_filters_past(cfg):
    now = datetime.now(tz.tzlocal())
    past = TripEvent(
        summary="Past", event_time=now - timedelta(hours=1), location="", feed_name=""
    )
    future = make_event(hours=2)
    result = build_upcoming_trips([past, future], now, AppState(), {})
    assert len(result) == 1
    assert result[0].summary == "Trip"


def test_build_upcoming_trips_back_to_back_flag(cfg):
    now = datetime.now(tz.tzlocal())
    event = make_event(hours=24)
    result = build_upcoming_trips(
        [event], now, AppState(), {}, back_to_back_ids={event.trip_id}
    )
    assert result[0].back_to_back is True


def test_build_upcoming_trips_excludes_active_trip():
    now = datetime.now(tz.tzlocal())
    active_event = make_event(hours=2, location="Metropolis")
    other_event = make_event(hours=24, location="Gotham")
    state = AppState(
        active_trip=ActiveTripState(
            trip_id=active_event.trip_id,
            target_soc=42,
            target_label="Metropolis",
            route_km=50.0,
        )
    )
    result = build_upcoming_trips([active_event, other_event], now, state, {})
    assert active_event.trip_id not in [getattr(r, "trip_id", None) for r in result]
    assert len(result) == 1
    assert result[0].location == "Gotham"


def test_build_upcoming_trips_soc_source_default():
    now = datetime.now(tz.tzlocal())
    event = make_event(hours=24, location="")
    result = build_upcoming_trips([event], now, AppState(), {})
    assert result[0].soc_source == TargetSource.DEFAULT
    assert result[0].target_label == "100% (default)"


def test_build_upcoming_trips_routing_failed_from_pre_results():
    now = datetime.now(tz.tzlocal())
    event = make_event(hours=24, location="Gotham")
    pre = {
        event.trip_id: LabelResult(
            "Gotham", 100, None, TargetSource.ROUTING_FAILED, None, None
        )
    }
    result = build_upcoming_trips([event], now, AppState(), pre)
    assert result[0].soc_source == TargetSource.ROUTING_FAILED
    assert result[0].target_label == "100% Gotham (routing failed)"
    assert result[0].target_soc_pct == 100


def test_build_upcoming_trips_routing_failed_fallback():
    """Event has a location but is not in pre_results (e.g. no home_address)."""
    now = datetime.now(tz.tzlocal())
    event = make_event(hours=24, location="Gotham")
    result = build_upcoming_trips([event], now, AppState(), {})
    assert result[0].soc_source == TargetSource.ROUTING_FAILED
    assert result[0].target_label == "100% Gotham (routing failed)"
    assert result[0].target_soc_pct == 100


# ── build_active_plan ─────────────────────────────────────────────────────


def test_build_active_plan_none_when_no_active():
    state = AppState()
    result = build_active_plan([], state)
    assert result is None


def test_build_active_plan_returns_plan():
    event = make_event(hours=24, location="Gotham")
    state = AppState(
        active_trip=ActiveTripState(
            trip_id=event.trip_id,
            target_soc=42,
            target_label="Gotham",
            route_km=70.0,
        )
    )
    result = build_active_plan([event], state)
    assert result is not None
    assert result.target_soc_pct == 42
    assert result.soc_source == TargetSource.ROUTED


def _sample_calc() -> RouteCalculation:
    return RouteCalculation(
        one_way_km=35.0,
        round_trip_km=70.0,
        consumption=20.0,
        round_trip_kwh=14.0,
        capacity_kwh=64.0,
        route_soc_pct=22,
        min_soc_pct=20,
        total_soc_pct=42,
    )


def test_build_active_plan_with_trip_cost():
    event = make_event(hours=24, location="Gotham")
    state = AppState(
        active_trip=ActiveTripState(
            trip_id=event.trip_id,
            target_soc=42,
            target_label="Gotham",
            route_km=70.0,
            calculation=_sample_calc(),
        )
    )
    result = build_active_plan([event], state, avg_price=0.30)
    assert result is not None
    assert result.trip_cost_eur == round(14.0 * 0.30, 2)
    assert result.calculation.trip_cost_eur == result.trip_cost_eur


def test_build_active_plan_without_avg_price():
    event = make_event(hours=24, location="Gotham")
    state = AppState(
        active_trip=ActiveTripState(
            trip_id=event.trip_id,
            target_soc=42,
            target_label="Gotham",
            route_km=70.0,
            calculation=_sample_calc(),
        )
    )
    result = build_active_plan([event], state, avg_price=None)
    assert result is not None
    assert result.trip_cost_eur is None


def test_build_active_plan_failed_source():
    event = make_event(hours=24, location="Gotham")
    state = AppState(
        active_trip=ActiveTripState(
            trip_id=event.trip_id,
            target_soc=100,
            target_label="100% Gotham (routing failed)",
            route_km=None,
        )
    )
    result = build_active_plan([event], state)
    assert result.soc_source == TargetSource.ROUTING_FAILED


# ── back_to_back_trip_ids ─────────────────────────────────────────────────


def test_back_to_back_ids_empty_when_not_active(cfg):
    state = AppState()
    events = [make_event(hours=1), make_event(hours=2)]
    result = back_to_back_trip_ids(events, state, cfg)
    assert result == set()


# ── trip_id uniqueness ────────────────────────────────────────────────────


def test_trip_id_unique_same_time_different_name():
    """Two events at the same time but with different names must have distinct trip_ids."""
    event_time = datetime.now(tz.tzlocal()) + timedelta(hours=6)
    e1 = TripEvent(summary="Work meeting", event_time=event_time, location="Office", feed_name="Cal")
    e2 = TripEvent(summary="School run", event_time=event_time, location="School", feed_name="Cal")
    assert e1.trip_id != e2.trip_id


def test_precalculate_labels_same_time_different_name_keeps_both_locations(cfg, vehicle):
    """Regression: two events at the same time must each retain their own location."""
    geocode_cache.set("Smallville, Midwest, Freedonia", Coords(10.123, 20.456))
    geocode_cache.set("Office", Coords(11.1, 21.1))
    geocode_cache.set("School", Coords(12.2, 22.2))
    route_cache.set("Smallville, Midwest, Freedonia", "Office", 20.0, 1200.0)
    route_cache.set("Smallville, Midwest, Freedonia", "School", 10.0, 600.0)

    event_time = datetime.now(tz.tzlocal()) + timedelta(hours=6)
    e1 = TripEvent(summary="Work meeting", event_time=event_time, location="Office", feed_name="Cal")
    e2 = TripEvent(summary="School run", event_time=event_time, location="School", feed_name="Cal")

    results = precalculate_labels(
        events=[e1, e2],
        cfg=cfg,
        active_trip_id=None,
        resolved_label=None,
        active_soc=None,
        active_route_km=None,
        vehicle=vehicle,
    )

    assert e1.trip_id in results
    assert e2.trip_id in results
    assert results[e1.trip_id].label == "Office"
    assert results[e2.trip_id].label == "School"
    # Distances must be independent — not overwritten by each other
    assert results[e1.trip_id].route_km == 20.0
    assert results[e2.trip_id].route_km == 10.0


def test_back_to_back_ids_within_window(cfg):
    now = datetime.now(tz.tzlocal())
    e1 = TripEvent(
        summary="T1", event_time=now + timedelta(hours=1), location="A", feed_name=""
    )
    e2 = TripEvent(
        summary="T2", event_time=now + timedelta(hours=6), location="B", feed_name=""
    )
    e3 = TripEvent(
        summary="T3", event_time=now + timedelta(hours=30), location="C", feed_name=""
    )

    state = AppState(
        active_trip=ActiveTripState(
            trip_id=e1.trip_id,
            target_soc=70,
            back_to_back=True,
        )
    )
    result = back_to_back_trip_ids([e1, e2, e3], state, cfg)
    assert e1.trip_id in result
    assert e2.trip_id in result
    assert e3.trip_id not in result
