"""Tests for the tick module — full cycle integration."""

import re
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
import responses as rsps_lib
from dateutil import tz
from icalendar import Calendar
from icalendar import Event as CalEvent

import departic.tick as tick_mod
from departic.config import (
    AgendaConfig,
    EvccConfig,
    EventMappingEntry,
    FeedConfig,
    Settings,
    VehicleConfig,
)
from departic.evcc import EvccAPI
from departic.models import AppState, TripEvent
from departic.tick import _get_evcc, _load_events, _run_controller, run_tick


@pytest.fixture(autouse=True)
def _reset_evcc_singleton():
    """Reset the module-level EVCC singleton before each test."""
    tick_mod._evcc = None
    yield
    tick_mod._evcc = None


def _cfg(evcc_url: str = "http://evcc.test") -> Settings:
    return Settings(
        evcc=EvccConfig(url=evcc_url, vehicle_title="bZ4X", home_address=""),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(
            feeds=[FeedConfig(url="http://cal.test/f.ics")],
            trip_mapping=[EventMappingEntry(tag="#trip", match="contains")],
        ),
    )


def _evcc_state() -> dict:
    return {
        "vehicles": {"bz4x": {"title": "bZ4X", "capacity": 64.0, "minSoc": 20}},
        "loadpoints": [],
        "statistics": {"30d": {"avgPrice": 0.25}},
    }


def _make_event(hours: float = 24) -> TripEvent:
    return TripEvent(
        summary="Test #trip",
        event_time=datetime.now(tz.tzlocal()) + timedelta(hours=hours),
        location="",
        feed_name="Cal",
    )


# ── _get_evcc ─────────────────────────────────────────────────────────────


def test_get_evcc_creates_instance():
    api = _get_evcc("http://evcc.test")
    assert api.base == "http://evcc.test/api"


def test_get_evcc_reuses_instance():
    api1 = _get_evcc("http://evcc.test")
    api2 = _get_evcc("http://evcc.test")
    assert api1 is api2


def test_get_evcc_recreates_on_url_change():
    api1 = _get_evcc("http://evcc1.test")
    api2 = _get_evcc("http://evcc2.test")
    assert api1 is not api2


# ── _load_events ──────────────────────────────────────────────────────────


@rsps_lib.activate
def test_load_events_returns_events():
    cfg = _cfg()
    cal = Calendar()
    cal.add("prodid", "-//T//EN")
    cal.add("version", "2.0")
    e = CalEvent()
    e.add("summary", "Go #trip")
    e.add("dtstart", datetime.now(tz.tzlocal()) + timedelta(hours=5))
    cal.add_component(e)
    rsps_lib.add(rsps_lib.GET, "http://cal.test/f.ics", body=cal.to_ical())

    events, error = _load_events(cfg)
    assert len(events) == 1
    assert error is None


def test_load_events_returns_error_on_exception():
    cfg = _cfg()
    with patch("departic.tick.parse_events", side_effect=RuntimeError("boom")):
        events, error = _load_events(cfg)
    assert events == []
    assert error == "Calendar unavailable"


# ── _run_controller ───────────────────────────────────────────────────────


@rsps_lib.activate
def test_run_controller_saves_state():
    cfg = _cfg()
    evcc = EvccAPI("http://evcc.test")
    event = _make_event(hours=24)

    rsps_lib.add(rsps_lib.GET, "http://evcc.test/api/state", json=_evcc_state())
    rsps_lib.add(
        rsps_lib.POST,
        re.compile(r"http://evcc\.test/api/vehicles/bz4x/plan/soc/100/"),
        json={"result": {}},
    )

    with patch("departic.tick.state_store") as mock_store:
        mock_store.load.return_value = AppState()
        new_state = _run_controller(cfg, [event], evcc)
        mock_store.save.assert_called_once()
    assert new_state.active_trip_id == event.trip_id


@rsps_lib.activate
def test_run_controller_catches_exception():
    cfg = _cfg()
    evcc = EvccAPI("http://evcc.test")

    with patch("departic.tick.state_store") as mock_store:
        mock_store.load.return_value = AppState()
        with patch("departic.tick.run_cycle", side_effect=RuntimeError("boom")):
            state = _run_controller(cfg, [], evcc)
    assert state.enabled is True


# ── run_tick ──────────────────────────────────────────────────────────────


def test_run_tick_config_error():
    with patch("departic.tick.Settings") as mock_settings:
        mock_settings.reload.return_value = None
        status = run_tick()
    assert "Configuration error" in status.tick_error


def test_run_tick_config_incomplete():
    incomplete = Settings(
        evcc=EvccConfig(url=""),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(feeds=[]),
    )
    with patch("departic.tick.Settings") as mock_settings:
        mock_settings.reload.return_value = incomplete
        status = run_tick()
    assert "incomplete" in status.tick_error


@rsps_lib.activate
def test_run_tick_full_cycle():
    cfg = _cfg()
    evcc = EvccAPI("http://evcc.test")
    event = _make_event(hours=24)

    # Three state calls: controller, vehicle pre-calc, avg_price
    rsps_lib.add(rsps_lib.GET, "http://evcc.test/api/state", json=_evcc_state())
    rsps_lib.add(rsps_lib.GET, "http://evcc.test/api/state", json=_evcc_state())
    rsps_lib.add(rsps_lib.GET, "http://evcc.test/api/state", json=_evcc_state())
    rsps_lib.add(
        rsps_lib.POST,
        re.compile(r"http://evcc\.test/api/vehicles/bz4x/plan/soc/100/"),
        json={"result": {}},
    )

    with (
        patch("departic.tick.Settings") as mock_settings,
        patch("departic.tick.state_store") as mock_store,
        patch("departic.tick.parse_events", return_value=([event], [])),
    ):
        mock_settings.reload.return_value = cfg
        mock_store.load.return_value = AppState()
        status = run_tick(evcc=evcc)

    assert status.tick_error is None
    assert len(status.upcoming_trips) == 0
    assert status.active_plan is not None
    assert status.active_plan.summary == event.summary
    assert status.enabled is True
    assert status.avg_price_eur == 0.25


@rsps_lib.activate
def test_run_tick_vehicle_fetch_fails_for_precalc():
    cfg = _cfg()
    evcc = EvccAPI("http://evcc.test")

    # First call: controller; second: vehicle pre-calc (fails); third: avg_price
    rsps_lib.add(rsps_lib.GET, "http://evcc.test/api/state", json=_evcc_state())
    rsps_lib.add(
        rsps_lib.GET,
        "http://evcc.test/api/state",
        body=rsps_lib.ConnectionError(),
    )
    rsps_lib.add(rsps_lib.GET, "http://evcc.test/api/state", json=_evcc_state())

    with (
        patch("departic.tick.Settings") as mock_settings,
        patch("departic.tick.state_store") as mock_store,
        patch("departic.tick.parse_events", return_value=([], [])),
    ):
        mock_settings.reload.return_value = cfg
        mock_store.load.return_value = AppState()
        status = run_tick(evcc=evcc)

    assert status.tick_error is None


@rsps_lib.activate
def test_run_tick_ical_errors_reported():
    cfg = _cfg()
    evcc = EvccAPI("http://evcc.test")

    # Controller (no events → no vehicle call), vehicle pre-calc, avg_price
    rsps_lib.add(rsps_lib.GET, "http://evcc.test/api/state", json=_evcc_state())
    rsps_lib.add(rsps_lib.GET, "http://evcc.test/api/state", json=_evcc_state())

    with (
        patch("departic.tick.Settings") as mock_settings,
        patch("departic.tick.state_store") as mock_store,
        patch(
            "departic.tick.parse_events",
            return_value=([], ["feed1: fetch failed"]),
        ),
    ):
        mock_settings.reload.return_value = cfg
        mock_store.load.return_value = AppState()
        status = run_tick(evcc=evcc)

    assert status.tick_error == "feed1: fetch failed"
