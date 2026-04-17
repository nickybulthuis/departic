"""Tests for iCal parsing."""

from datetime import UTC, date, datetime, timedelta

import pytest
import requests
import responses as rsps_lib
from dateutil import tz
from icalendar import Calendar, Event

from departic.config import (
    AgendaConfig,
    EvccConfig,
    EventMappingEntry,
    FeedConfig,
    Settings,
    VehicleConfig,
)
from departic.http_session import build_session
from departic.ical import (
    _in_window,
    _is_trip_event,
    _to_local_dt,
    fetch_ical,
    parse_events,
    parse_feed,
)


def _make_cfg(*tags: tuple[str, str]) -> Settings:
    mapping = [EventMappingEntry(tag=t, match=m) for t, m in tags]
    return Settings(
        evcc=EvccConfig(url="http://evcc.test"),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(feeds=[], trip_mapping=mapping),
    )


def _make_cal(*events: dict) -> Calendar:
    cal = Calendar()
    cal.add("prodid", "-//Test//EN")
    cal.add("version", "2.0")
    for ev in events:
        e = Event()
        e.add("summary", ev.get("summary", "Event"))
        if "dtstart" in ev:
            e.add("dtstart", ev["dtstart"])
        if "location" in ev:
            e.add("location", ev["location"])
        if "description" in ev:
            e.add("description", ev["description"])
        cal.add_component(e)
    return cal


# ── _to_local_dt ──────────────────────────────────────────────────────────


def test_to_local_dt_aware_datetime():
    dt = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
    result = _to_local_dt(dt)
    assert result.tzinfo is not None


def test_to_local_dt_naive_datetime():
    dt = datetime(2026, 4, 1, 10, 0)  # noqa: DTZ001 — testing the naive-datetime branch
    result = _to_local_dt(dt)
    assert result.tzinfo is not None


def test_to_local_dt_date():
    d = date(2026, 4, 1)
    result = _to_local_dt(d)
    assert isinstance(result, datetime)
    assert result.tzinfo is not None


# ── _is_trip_event ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("summary", "description", "tags", "expected"),
    [
        ("Holiday #trip", "", [("#trip", "contains")], True),
        ("Holiday", "", [("#trip", "contains")], False),
        ("Trip to Smallville", "", [("Trip", "prefix")], True),
        ("My Trip", "", [("Trip", "prefix")], False),
        # Tag found in description rather than summary
        ("Holiday", "#trip", [("#trip", "contains")], True),
        # Matching is case-insensitive
        ("holiday #trip", "", [("#TRIP", "contains")], True),
        # No mappings configured → never a trip
        ("#trip event", "", [], False),
    ],
)
def test_is_trip_event(summary, description, tags, expected):
    cfg = _make_cfg(*tags)
    assert _is_trip_event(summary, description, cfg) is expected


# ── _in_window ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(hours=1), True),  # near future → in window
        (
            timedelta(days=15),
            False,
        ),  # too far future → outside window (14-day lookahead)
        (timedelta(minutes=-30), True),  # recent past (grace period) → in window
        (timedelta(hours=-2), False),  # old past → outside window
    ],
)
def test_in_window(delta, expected):
    now = datetime.now(tz.tzlocal())
    assert _in_window(now + delta, now, lookahead_days=14) is expected


# ── fetch_ical ────────────────────────────────────────────────────────────


@rsps_lib.activate
def test_fetch_ical_success():
    cal = _make_cal({"summary": "Test", "dtstart": datetime.now(tz.tzlocal())})
    rsps_lib.add(rsps_lib.GET, "http://cal.test/feed.ics", body=cal.to_ical())
    session = build_session(max_retries=0)
    result = fetch_ical("http://cal.test/feed.ics", session=session)
    assert isinstance(result, Calendar)


@rsps_lib.activate
def test_fetch_ical_http_error():
    rsps_lib.add(rsps_lib.GET, "http://cal.test/feed.ics", status=400)
    session = build_session(max_retries=0)
    with pytest.raises(requests.HTTPError):
        fetch_ical("http://cal.test/feed.ics", session=session)


@rsps_lib.activate
def test_fetch_ical_retries_on_failure():
    """Succeed on the second attempt after the first one fails."""
    cal = _make_cal({"summary": "Test", "dtstart": datetime.now(tz.tzlocal())})
    rsps_lib.add(rsps_lib.GET, "http://cal.test/feed.ics", status=503)
    rsps_lib.add(rsps_lib.GET, "http://cal.test/feed.ics", body=cal.to_ical())

    session = build_session(max_retries=1, backoff_factor=0)
    result = fetch_ical("http://cal.test/feed.ics", session=session)
    assert isinstance(result, Calendar)


@rsps_lib.activate
def test_fetch_ical_retries_exhausted():
    """All retries fail — exception is raised."""
    rsps_lib.add(rsps_lib.GET, "http://cal.test/feed.ics", status=503)
    rsps_lib.add(rsps_lib.GET, "http://cal.test/feed.ics", status=503)
    session = build_session(max_retries=1, backoff_factor=0)
    with pytest.raises(requests.HTTPError):
        fetch_ical("http://cal.test/feed.ics", session=session)


# ── parse_feed ────────────────────────────────────────────────────────────


def test_parse_feed_extracts_trip():
    cfg = _make_cfg(("#trip", "contains"))
    departure = datetime.now(tz.tzlocal()) + timedelta(hours=5)
    cal = _make_cal(
        {"summary": "Holiday #trip", "dtstart": departure, "location": "Metropolis"}
    )
    events = parse_feed(cal, "TestCal", cfg)
    assert len(events) == 1
    assert events[0].summary == "Holiday #trip"
    assert events[0].location == "Metropolis"
    assert events[0].feed_name == "TestCal"


def test_parse_feed_skips_non_trip():
    cfg = _make_cfg(("#trip", "contains"))
    departure = datetime.now(tz.tzlocal()) + timedelta(hours=5)
    cal = _make_cal({"summary": "Dentist", "dtstart": departure})
    assert len(parse_feed(cal, "Cal", cfg)) == 0


def test_parse_feed_skips_old_event():
    cfg = _make_cfg(("#trip", "contains"))
    departure = datetime.now(tz.tzlocal()) - timedelta(days=2)
    cal = _make_cal({"summary": "Old #trip", "dtstart": departure})
    assert len(parse_feed(cal, "Cal", cfg)) == 0


def test_parse_feed_skips_event_without_dtstart():
    cfg = _make_cfg(("#trip", "contains"))
    cal = Calendar()
    cal.add("prodid", "-//T//EN")
    cal.add("version", "2.0")
    e = Event()
    e.add("summary", "No date #trip")
    cal.add_component(e)
    assert len(parse_feed(cal, "Cal", cfg)) == 0


# ── parse_events ──────────────────────────────────────────────────────────


@rsps_lib.activate
def test_parse_events_success():
    cfg = Settings(
        evcc=EvccConfig(url="http://evcc.test"),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(
            feeds=[FeedConfig(url="http://cal.test/f.ics", name="MyCal")],
            trip_mapping=[EventMappingEntry(tag="#trip", match="contains")],
        ),
    )
    departure = datetime.now(tz.tzlocal()) + timedelta(hours=5)
    cal = _make_cal({"summary": "Go #trip", "dtstart": departure, "location": "Berlin"})
    rsps_lib.add(rsps_lib.GET, "http://cal.test/f.ics", body=cal.to_ical())

    events, errors = parse_events(cfg)
    assert len(events) == 1
    assert errors == []


@rsps_lib.activate
def test_parse_events_feed_http_error(monkeypatch):
    monkeypatch.setattr(
        "departic.ical.build_session",
        lambda **_kw: build_session(max_retries=0, backoff_factor=0),
    )
    cfg = Settings(
        evcc=EvccConfig(url="http://evcc.test"),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(
            feeds=[FeedConfig(url="http://cal.test/bad.ics", name="Bad")],
            trip_mapping=[EventMappingEntry(tag="#trip", match="contains")],
        ),
    )
    rsps_lib.add(rsps_lib.GET, "http://cal.test/bad.ics", status=400)

    events, errors = parse_events(cfg)
    assert len(events) == 0
    assert len(errors) == 1
    assert "Bad" in errors[0]


@rsps_lib.activate
def test_parse_events_feed_value_error(monkeypatch):
    monkeypatch.setattr(
        "departic.ical.build_session",
        lambda **_kw: build_session(max_retries=0, backoff_factor=0),
    )
    cfg = Settings(
        evcc=EvccConfig(url="http://evcc.test"),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(
            feeds=[FeedConfig(url="http://cal.test/bad.ics", name="Bad")],
            trip_mapping=[EventMappingEntry(tag="#trip", match="contains")],
        ),
    )
    rsps_lib.add(rsps_lib.GET, "http://cal.test/bad.ics", body=b"not valid ical")

    events, errors = parse_events(cfg)
    assert len(events) == 0
    assert len(errors) == 1


@rsps_lib.activate
def test_parse_events_sorts_by_event_time():
    cfg = Settings(
        evcc=EvccConfig(url="http://evcc.test"),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(
            feeds=[FeedConfig(url="http://cal.test/f.ics", name="Cal")],
            trip_mapping=[EventMappingEntry(tag="#trip", match="contains")],
        ),
    )
    now = datetime.now(tz.tzlocal())
    cal = _make_cal(
        {"summary": "Later #trip", "dtstart": now + timedelta(hours=10)},
        {"summary": "Sooner #trip", "dtstart": now + timedelta(hours=2)},
    )
    rsps_lib.add(rsps_lib.GET, "http://cal.test/f.ics", body=cal.to_ical())

    events, _ = parse_events(cfg)
    assert len(events) == 2
    assert events[0].summary == "Sooner #trip"
    assert events[1].summary == "Later #trip"
