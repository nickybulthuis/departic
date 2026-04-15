"""Tests for web API routes."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from departic.config import (
    AgendaConfig,
    EvccConfig,
    FeedConfig,
    Settings,
    VehicleConfig,
)
from departic.models import AppState, EvccLiveStatus
from departic.web.router import (
    _feed_color,
    _fmt_countdown,
    _fmt_date,
    _fmt_departure,
    _fmt_dow,
    _fmt_duration,
    _fmt_eur,
    _fmt_power,
    _fmt_time,
    _is_departed,
    _is_soon,
    router,
)

_app = FastAPI()
_app.include_router(router)


@pytest.fixture
async def client():
    transport = ASGITransport(app=_app)
    base_url = f"http://{uuid4().hex}.test"
    async with AsyncClient(
        transport=transport, base_url=base_url, follow_redirects=False
    ) as c:
        yield c


# ── Helper function tests ─────────────────────────────────────────────────


def test_feed_color_empty():
    assert _feed_color("") == ""


def test_feed_color_consistent():
    c1 = _feed_color("Work")
    c2 = _feed_color("Work")
    assert c1 == c2
    assert c1.startswith("is-")


def test_feed_color_different_names():
    # Different names may produce different colours (not guaranteed but likely)
    assert isinstance(_feed_color("Family"), str)


def test_fmt_dow():
    assert _fmt_dow("2026-04-06T10:00:00+00:00") == "Mon"


def test_fmt_date():
    assert _fmt_date("2026-04-06T10:00:00+00:00") == "6 Apr"


def test_fmt_time():
    # Use a date that is within the next 7 days but not today → "Www HH:mm"
    base = datetime.now(UTC).replace(hour=14, minute=30, second=0, microsecond=0)
    future = base + timedelta(days=2)
    expected_dow = future.strftime("%a")
    assert _fmt_time(future.isoformat()) == f"{expected_dow} 14:30"


def test_fmt_time_today():
    now = datetime.now(UTC)
    iso = now.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    assert _fmt_time(iso) == "09:00"


def test_fmt_countdown_departed():
    assert _fmt_countdown("2020-01-01T00:00:00+00:00") == "departed"


def test_fmt_countdown_minutes():
    future = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    result = _fmt_countdown(future)
    assert result.endswith("m")


def test_fmt_countdown_hours():
    future = (datetime.now(UTC) + timedelta(hours=5, minutes=15)).isoformat()
    result = _fmt_countdown(future)
    assert "h" in result
    assert "m" in result


def test_fmt_countdown_days():
    future = (datetime.now(UTC) + timedelta(days=3, hours=2)).isoformat()
    result = _fmt_countdown(future)
    assert "d" in result
    assert "h" in result


def test_is_soon_true():
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert _is_soon(future) is True


def test_is_soon_false_far():
    future = (datetime.now(UTC) + timedelta(hours=10)).isoformat()
    assert _is_soon(future) is False


def test_is_soon_false_past():
    assert _is_soon("2020-01-01T00:00:00+00:00") is False


def test_fmt_eur_value():
    assert _fmt_eur(3.5) == "€3.50"


def test_fmt_eur_none():
    assert _fmt_eur(None) == ""


def test_fmt_eur_zero():
    assert _fmt_eur(0.0) == "€0.00"


def test_is_departed_true():
    assert _is_departed("2020-01-01T00:00:00+00:00") is True


def test_is_departed_false():
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert _is_departed(future) is False


# ── Duration / leave-by helpers ────────────────────────────────────────────


def test_fmt_duration_none():
    assert _fmt_duration(None) == ""


def test_fmt_duration_minutes():
    assert _fmt_duration(45) == "45 min"


def test_fmt_duration_hours_and_minutes():
    assert _fmt_duration(90) == "1h 30m"


def test_fmt_duration_exact_hours():
    assert _fmt_duration(120) == "2h"


def test_fmt_departure_subtracts_duration():
    # Event 2 days from now at 13:00 → departure at 12:00 (60 min drive)
    base = datetime.now(UTC).replace(hour=13, minute=0, second=0, microsecond=0)
    event = base + timedelta(days=2)
    departure = event - timedelta(minutes=60)
    expected_dow = departure.strftime("%a")
    assert _fmt_departure(event.isoformat(), 60) == f"{expected_dow} 12:00"


def test_fmt_departure_today():
    # Build event time = today at 23:30, departure = today 22:00
    today = datetime.now(UTC).replace(hour=23, minute=30, second=0, microsecond=0)
    iso = today.isoformat()
    result = _fmt_departure(iso, 90)
    assert ":" in result
    # Should contain only time (no day prefix) if departure is today
    parts = result.split()
    assert len(parts) == 1  # just "HH:MM"


def test_fmt_departure_none():
    assert _fmt_departure("2026-04-06T13:00:00+00:00", None) == ""


def test_fmt_power_watts():
    assert _fmt_power(0.428) == "428\u2009W"


def test_fmt_power_watts_boundary():
    assert _fmt_power(0.9999) == "1000\u2009W"


def test_fmt_power_kw():
    assert _fmt_power(1.0) == "1.0\u2009kW"


def test_fmt_power_kw_large():
    assert _fmt_power(10.4) == "10.4\u2009kW"


def test_fmt_power_none():
    assert _fmt_power(None) == "-"


# ── Route tests ────────────────────────────────────────────────────────────


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_api_status(client):
    with patch("departic.web.router.scheduler") as mock_sched:
        mock_sched.get_status.return_value = {"enabled": True, "upcoming_trips": []}
        r = await client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["enabled"] is True


async def test_api_toggle(client):
    with (
        patch("departic.web.router.state_store") as mock_store,
        patch("departic.web.router.scheduler") as mock_sched,
        patch("departic.web.router.notify") as mock_notify,
        patch("departic.web.router.Settings") as mock_settings,
    ):
        mock_store.load.return_value = AppState(enabled=True)
        mock_settings.get.return_value = Settings(
            evcc=EvccConfig(url="http://evcc.test"),
            vehicle=VehicleConfig(),
            agenda=AgendaConfig(feeds=[]),
        )
        r = await client.post("/api/toggle")
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    mock_store.save.assert_called_once()
    mock_sched.set_enabled.assert_called_once_with(False)
    mock_notify.assert_called_once()


async def test_api_trigger_success(client):
    with patch("departic.web.router.scheduler"):
        r = await client.post("/api/trigger")
    assert r.status_code == 303
    assert r.headers["location"] == "/"


async def test_api_trigger_failure(client):
    with patch("departic.web.router.scheduler") as mock_sched:
        mock_sched.scheduler.modify_job.side_effect = RuntimeError("no job")
        r = await client.post("/api/trigger")
    # Still redirects even on failure
    assert r.status_code == 303
    assert r.headers["location"] == "/"


async def test_set_log_level_valid(client):
    r = await client.post("/api/loglevel", data={"level": "debug"})
    assert r.status_code == 303
    assert r.headers["location"] == "/"


async def test_set_log_level_invalid(client):
    r = await client.post("/api/loglevel", data={"level": "nonsense"})
    assert r.status_code == 400


async def test_get_log_level(client):
    r = await client.get("/api/loglevel")
    assert r.status_code == 200
    assert "level" in r.json()


async def test_dashboard_configured(client):
    cfg = Settings(
        evcc=EvccConfig(url="http://evcc.test"),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(feeds=[FeedConfig(url="http://x")]),
    )
    with (
        patch("departic.web.router.Settings") as mock_settings,
        patch("departic.web.router.scheduler") as mock_sched,
    ):
        mock_settings.get.return_value = cfg
        mock_sched.get_status.return_value = {
            "enabled": True,
            "upcoming_trips": [],
            "active_plan": None,
            "tick_updated": None,
            "tick_error": None,
            "version": "0.0.0",
        }
        r = await client.get("/")
    assert r.status_code == 200


async def test_dashboard_unconfigured(client):
    with patch("departic.web.router.Settings") as mock_settings:
        mock_settings.get.return_value = None
        r = await client.get("/")
    assert r.status_code == 200


async def test_api_evcc_not_configured(client):
    with patch("departic.web.router.Settings") as mock_settings:
        mock_settings.get.return_value = None
        r = await client.get("/api/evcc")
    assert r.status_code == 404


async def test_api_evcc_no_loadpoint(client):
    cfg = Settings(
        evcc=EvccConfig(url="http://evcc.test"),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(feeds=[FeedConfig(url="http://x")]),
    )
    with (
        patch("departic.web.router.Settings") as mock_settings,
        patch("departic.web.router.EvccAPI") as mock_api_cls,
    ):
        mock_settings.get.return_value = cfg
        mock_api_cls.return_value.get_loadpoint_status.return_value = None
        r = await client.get("/api/evcc")
    assert r.status_code == 503


async def test_api_evcc_success(client):
    cfg = Settings(
        evcc=EvccConfig(url="http://evcc.test"),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(feeds=[FeedConfig(url="http://x")]),
    )
    status = EvccLiveStatus(
        vehicle_soc_pct=62, charging_mode="pv", vehicle_connected=True
    )
    with (
        patch("departic.web.router.Settings") as mock_settings,
        patch("departic.web.router.EvccAPI") as mock_api_cls,
    ):
        mock_settings.get.return_value = cfg
        mock_api_cls.return_value.get_loadpoint_status.return_value = status
        mock_api_cls.return_value.get_interval.return_value = 30
        r = await client.get("/api/evcc")
    assert r.status_code == 200
    data = r.json()
    assert data["vehicle_soc_pct"] == 62
    assert data["interval"] == 30


async def test_dashboard_evcc_poll_interval(client):
    """Dashboard passes evcc_poll_interval from get_interval() to the template."""
    cfg = Settings(
        evcc=EvccConfig(url="http://evcc.test"),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(feeds=[FeedConfig(url="http://x")]),
    )
    with (
        patch("departic.web.router.Settings") as mock_settings,
        patch("departic.web.router.scheduler") as mock_sched,
        patch("departic.web.router.EvccAPI") as mock_api_cls,
    ):
        mock_settings.get.return_value = cfg
        mock_sched.get_status.return_value = {
            "enabled": True,
            "upcoming_trips": [],
            "active_plan": None,
            "tick_updated": None,
            "tick_error": None,
            "version": "0.0.0",
            "evcc_status": {"charging_mode": "pv", "vehicle_connected": False},
        }
        mock_api_cls.return_value.get_interval.return_value = 15
        r = await client.get("/")
    assert r.status_code == 200
    assert b'data-evcc-interval="15"' in r.content


async def test_dashboard_evcc_poll_interval_exception(client):
    """Dashboard falls back to 30s if get_interval() raises."""
    cfg = Settings(
        evcc=EvccConfig(url="http://evcc.test"),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(feeds=[FeedConfig(url="http://x")]),
    )
    with (
        patch("departic.web.router.Settings") as mock_settings,
        patch("departic.web.router.scheduler") as mock_sched,
        patch("departic.web.router.EvccAPI") as mock_api_cls,
    ):
        mock_settings.get.return_value = cfg
        mock_sched.get_status.return_value = {
            "enabled": True,
            "upcoming_trips": [],
            "active_plan": None,
            "tick_updated": None,
            "tick_error": None,
            "version": "0.0.0",
        }
        mock_api_cls.return_value.get_interval.side_effect = RuntimeError("fail")
        r = await client.get("/")
    assert r.status_code == 200
