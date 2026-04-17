"""
iCal parsing.

Supports multiple calendar feeds. Events from all feeds are combined
and sorted by departure time.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import requests
from dateutil import tz
from icalendar import Calendar

from departic.http_session import build_session
from departic.models import TripEvent

if TYPE_CHECKING:
    from departic.config import Settings

log = logging.getLogger(__name__)


def fetch_ical(url: str, *, session: requests.Session | None = None) -> Calendar:
    """Fetch and parse an iCal feed (retries are handled by the session)."""
    s = session or build_session()
    response = s.get(url, timeout=10)
    response.raise_for_status()
    return Calendar.from_ical(response.content)


def _to_local_dt(value: datetime | date) -> datetime:
    local_tz = tz.tzlocal()

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=local_tz)
        return value.astimezone(local_tz)

    return datetime(value.year, value.month, value.day, tzinfo=local_tz)


def _is_trip_event(summary: str, description: str, cfg: Settings) -> bool:
    summary_lower = summary.lower()
    combined_lower = f"{summary} {description}".lower()

    for mapping in cfg.agenda.trip_mapping:
        tag = mapping.tag.lower()

        if mapping.match == "prefix":
            if summary_lower.startswith(tag):
                return True
        elif tag in combined_lower:
            return True

    return False


def _in_window(event_time: datetime, now: datetime, lookahead_days: int) -> bool:
    return (
        now - timedelta(hours=1) <= event_time <= now + timedelta(days=lookahead_days)
    )


def parse_feed(cal: Calendar, feed_name: str, cfg: Settings) -> list[TripEvent]:
    """Extract trip events from a single calendar feed."""
    now = datetime.now(tz.tzlocal())
    lookahead_days = cfg.agenda.lookahead_days
    events: list[TripEvent] = []

    for component in cal.walk("VEVENT"):
        summary = str(component.get("SUMMARY", "")).strip()
        description = str(component.get("DESCRIPTION", "")).strip()
        location = str(component.get("LOCATION", "")).strip()

        dtstart = component.get("DTSTART")
        if dtstart is None:
            continue

        event_time = _to_local_dt(dtstart.dt)
        if not _in_window(event_time, now, lookahead_days):
            continue

        if not _is_trip_event(summary, description, cfg):
            continue

        event = TripEvent(
            summary=summary,
            event_time=event_time,
            location=location,
            feed_name=feed_name,
        )
        events.append(event)

        log.info(
            "Trip event: '%s' [%s] | event_time: %s | location: %r",
            summary,
            feed_name or "unnamed",
            event_time.strftime("%d-%m %H:%M"),
            location or "(none)",
        )

    return events


def parse_events(cfg: Settings) -> tuple[list[TripEvent], list[str]]:
    """
    Fetch and parse all configured calendar feeds.

    Returns:
        (events sorted by event_time, list of error strings)
    """
    events: list[TripEvent] = []
    errors: list[str] = []

    for feed in cfg.agenda.feeds:
        feed_label = feed.name or feed.url

        try:
            cal = fetch_ical(feed.url)
            events.extend(parse_feed(cal, feed.name, cfg))
        except requests.RequestException:
            # Feed-level boundary: one broken calendar must not block others.
            log.exception("Failed to fetch feed %r", feed_label)
            errors.append(f"{feed_label}: fetch failed")
        except ValueError:
            log.exception("Failed to parse feed %r", feed_label)
            errors.append(f"{feed_label}: parse failed")
        except Exception:
            # Safety boundary for malformed calendar.
            log.exception("Unexpected error while processing feed %r", feed_label)
            errors.append(f"{feed_label}: failed")

    events.sort(key=lambda event: event.event_time)
    return events, errors
