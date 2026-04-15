"""
Tests for the Departic notifier module.
"""

from unittest.mock import MagicMock, patch

import pytest

from departic.config import (
    EventNotificationConfig,
    EventsConfig,
    NotificationsConfig,
)
from departic.notifier import (
    NotifyEvent,
    _format_message,
    _get_apprise,
    notify,
    reset,
)


@pytest.fixture(autouse=True)
def _reset_notifier():
    """Ensure module-level state is clean before and after every test."""
    reset()
    yield
    reset()


def _cfg(*urls: str, **events_kw: object) -> NotificationsConfig:
    events = EventsConfig(**events_kw) if events_kw else EventsConfig()
    return NotificationsConfig(urls=list(urls), events=events)


# ── _format_message (defaults) ────────────────────────────────────────────


def test_format_plan_activated():
    title, body = _format_message(
        NotifyEvent.PLAN_ACTIVATED,
        summary="Trip to Berlin",
        soc_pct=72,
        deadline="Fri 18-04 08:00",
        route_km=245.0,
    )
    assert "Plan activated" in title
    assert "Trip to Berlin" in body
    assert "72%" in body
    assert "245 km" in body


def test_format_plan_activated_no_route():
    title, body = _format_message(
        NotifyEvent.PLAN_ACTIVATED,
        summary="Trip to Berlin",
        soc_pct=100,
        deadline="Fri 18-04 08:00",
    )
    assert "km" not in body
    assert "100%" in body


def test_format_plan_updated():
    title, body = _format_message(
        NotifyEvent.PLAN_UPDATED,
        summary="Trip to Berlin",
        old_soc_pct=80,
        new_soc_pct=72,
    )
    assert "updated" in title.lower()
    assert "80%" in body
    assert "72%" in body


def test_format_plan_cleared():
    title, body = _format_message(
        NotifyEvent.PLAN_CLEARED,
        summary="Trip to Berlin",
    )
    assert "cleared" in title.lower()
    assert "Trip to Berlin" in body


def test_format_routing_failed():
    title, body = _format_message(
        NotifyEvent.ROUTING_FAILED,
        summary="Trip to Berlin",
        location="Berlin, Germany",
    )
    assert "Routing failed" in title
    assert "Trip to Berlin" in body
    assert "Berlin, Germany" in body
    assert "100%" in body


def test_format_toggled_enabled():
    title, body = _format_message(NotifyEvent.TOGGLED, enabled=True)
    assert "enabled" in title.lower()
    assert "enabled" in body.lower()


def test_format_toggled_disabled():
    title, body = _format_message(NotifyEvent.TOGGLED, enabled=False)
    assert "disabled" in title.lower()
    assert "disabled" in body.lower()


# ── _format_message (custom templates) ────────────────────────────────────


def test_format_custom_title_and_body():
    evt_cfg = EventNotificationConfig(
        title="EV: {summary}",
        body="Charge to {soc_pct}% before {deadline}",
    )
    title, body = _format_message(
        NotifyEvent.PLAN_ACTIVATED,
        evt_cfg,
        summary="Road trip",
        soc_pct=80,
        deadline="Sat 09:00",
    )
    assert title == "EV: Road trip"
    assert body == "Charge to 80% before Sat 09:00"


def test_format_custom_title_default_body():
    evt_cfg = EventNotificationConfig(title="Custom title")
    title, body = _format_message(
        NotifyEvent.PLAN_CLEARED,
        evt_cfg,
        summary="Trip to Berlin",
    )
    assert title == "Custom title"
    assert "Trip to Berlin" in body  # default body still used


def test_format_custom_body_default_title():
    evt_cfg = EventNotificationConfig(body="Bye {summary}")
    title, body = _format_message(
        NotifyEvent.PLAN_CLEARED,
        evt_cfg,
        summary="Trip to Berlin",
    )
    assert "cleared" in title.lower()  # default title
    assert body == "Bye Trip to Berlin"


def test_format_custom_missing_placeholder_kept():
    """Unknown {placeholders} are preserved as-is."""
    evt_cfg = EventNotificationConfig(body="Hello {unknown_var}!")
    _, body = _format_message(NotifyEvent.PLAN_CLEARED, evt_cfg, summary="X")
    assert body == "Hello {unknown_var}!"


def test_format_custom_toggled_template():
    evt_cfg = EventNotificationConfig(
        title="System {label}",
        body="Departic is now {label}. Enabled={enabled}",
    )
    title, body = _format_message(NotifyEvent.TOGGLED, evt_cfg, enabled=True)
    assert title == "System enabled"
    assert "enabled" in body
    assert "True" in body


# ── _get_apprise ──────────────────────────────────────────────────────────


def test_get_apprise_not_configured():
    assert _get_apprise(NotificationsConfig()) is None


def test_get_apprise_returns_instance():
    ap = _get_apprise(_cfg("json://localhost"))
    assert ap is not None


def test_get_apprise_reuses_instance():
    cfg = _cfg("json://localhost")
    first = _get_apprise(cfg)
    second = _get_apprise(cfg)
    assert first is second


def test_get_apprise_recreates_on_url_change():
    first = _get_apprise(_cfg("json://localhost"))
    second = _get_apprise(_cfg("json://other"))
    assert first is not second


# ── notify ────────────────────────────────────────────────────────────────


def test_notify_noop_when_not_configured():
    """No URLs → no Apprise call, no crash."""
    notify(NotificationsConfig(), NotifyEvent.PLAN_ACTIVATED, summary="Test")


@patch("departic.notifier._get_apprise")
def test_notify_calls_apprise(mock_get):
    mock_ap = MagicMock()
    mock_get.return_value = mock_ap

    cfg = _cfg("json://localhost")
    notify(cfg, NotifyEvent.PLAN_ACTIVATED, summary="Test", soc_pct=80, deadline="now")

    mock_ap.notify.assert_called_once()
    call_kwargs = mock_ap.notify.call_args
    assert "Plan activated" in call_kwargs.kwargs["title"]


@patch("departic.notifier._get_apprise")
def test_notify_handles_apprise_exception(mock_get):
    mock_ap = MagicMock()
    mock_ap.notify.side_effect = RuntimeError("network error")
    mock_get.return_value = mock_ap

    # Should not raise
    notify(_cfg("json://localhost"), NotifyEvent.PLAN_CLEARED, summary="Test")


@patch("departic.notifier._get_apprise")
def test_notify_skips_disabled_event(mock_get):
    """When an event type is disabled, Apprise is never called."""
    mock_ap = MagicMock()
    mock_get.return_value = mock_ap

    cfg = NotificationsConfig(
        urls=["json://localhost"],
        events=EventsConfig(
            plan_activated=EventNotificationConfig(enabled=False),
        ),
    )
    notify(cfg, NotifyEvent.PLAN_ACTIVATED, summary="Test", soc_pct=80, deadline="now")

    mock_ap.notify.assert_not_called()


@patch("departic.notifier._get_apprise")
def test_notify_sends_enabled_event(mock_get):
    """Explicitly enabled event → notification sent."""
    mock_ap = MagicMock()
    mock_get.return_value = mock_ap

    cfg = NotificationsConfig(
        urls=["json://localhost"],
        events=EventsConfig(
            plan_cleared=EventNotificationConfig(enabled=True),
        ),
    )
    notify(cfg, NotifyEvent.PLAN_CLEARED, summary="Trip")

    mock_ap.notify.assert_called_once()


@patch("departic.notifier._get_apprise")
def test_notify_uses_custom_template(mock_get):
    """Custom title/body from config are rendered with context variables."""
    mock_ap = MagicMock()
    mock_get.return_value = mock_ap

    cfg = NotificationsConfig(
        urls=["json://localhost"],
        events=EventsConfig(
            plan_activated=EventNotificationConfig(
                title="EV Plan",
                body="Charge {soc_pct}% for {summary}",
            ),
        ),
    )
    notify(cfg, NotifyEvent.PLAN_ACTIVATED, summary="Berlin", soc_pct=72, deadline="x")

    call_kwargs = mock_ap.notify.call_args.kwargs
    assert call_kwargs["title"] == "EV Plan"
    assert call_kwargs["body"] == "Charge 72% for Berlin"


