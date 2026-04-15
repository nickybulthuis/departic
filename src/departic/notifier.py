"""
Departic notifier — sends push notifications on important events via Apprise.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING

import apprise

if TYPE_CHECKING:
    from departic.config import EventNotificationConfig, NotificationsConfig

log = logging.getLogger(__name__)

# Module-level Apprise instance — lazily initialised.
_apprise: apprise.Apprise | None = None
_configured_urls: list[str] = []


class NotifyEvent(StrEnum):
    """Events that can trigger a notification."""

    PLAN_ACTIVATED = "plan_activated"
    PLAN_UPDATED = "plan_updated"
    PLAN_CLEARED = "plan_cleared"
    ROUTING_FAILED = "routing_failed"
    TOGGLED = "toggled"


# ── Built-in defaults ─────────────────────────────────────────────────────

_DEFAULT_TITLES: dict[NotifyEvent, str] = {
    NotifyEvent.PLAN_ACTIVATED: "🔋 Plan activated",
    NotifyEvent.PLAN_UPDATED: "🔄 Plan updated",
    NotifyEvent.PLAN_CLEARED: "✅ Plan cleared",
    NotifyEvent.ROUTING_FAILED: "⚠️ Routing failed",
    NotifyEvent.TOGGLED: "{emoji} Departic {label}",
}

_DEFAULT_BODIES: dict[NotifyEvent, str] = {
    NotifyEvent.PLAN_ACTIVATED: "{summary} — {soc_pct}% SoC by {deadline}{route_info}",
    NotifyEvent.PLAN_UPDATED: "{summary} — {old_soc_pct}% → {new_soc_pct}% SoC",
    NotifyEvent.PLAN_CLEARED: "Charging plan removed (was: {summary}).",
    NotifyEvent.ROUTING_FAILED: "{summary} → {location} — using default 100% SoC.",
    NotifyEvent.TOGGLED: "Departic has been {label}.",
}


def _get_apprise(cfg: NotificationsConfig) -> apprise.Apprise | None:
    """Return the module-level Apprise instance, (re-)creating if URLs changed."""
    global _apprise, _configured_urls  # noqa: PLW0603

    if not cfg.is_configured():
        return None

    if _apprise is not None and _configured_urls == cfg.urls:
        return _apprise

    ap = apprise.Apprise()
    for url in cfg.urls:
        ap.add(url)

    _apprise = ap
    _configured_urls = list(cfg.urls)
    return ap


def _event_config(
    cfg: NotificationsConfig, event: NotifyEvent
) -> EventNotificationConfig:
    """Look up the per-event config from the events block."""
    return getattr(cfg.events, event.value)


def _enrich_kwargs(event: NotifyEvent, kwargs: dict[str, object]) -> dict[str, object]:
    """Add computed convenience variables to the template context."""
    enriched = dict(kwargs)
    if event == NotifyEvent.PLAN_ACTIVATED:
        route_km = enriched.get("route_km")
        enriched["route_info"] = f" ({route_km:.0f} km)" if route_km else ""
    elif event == NotifyEvent.TOGGLED:
        enabled = enriched.get("enabled", False)
        enriched["label"] = "enabled" if enabled else "disabled"
        enriched["emoji"] = "🟢" if enabled else "🔴"
    return enriched


def _format_message(
    event: NotifyEvent,
    event_cfg: EventNotificationConfig | None = None,
    **kwargs: object,
) -> tuple[str, str]:
    """Return (title, body) for a notification event.

    If the per-event config provides custom title/body templates they are used;
    otherwise the built-in defaults are rendered.
    """
    ctx = _enrich_kwargs(event, kwargs)

    title_tpl = (event_cfg.title if event_cfg and event_cfg.title else None) or _DEFAULT_TITLES[event]
    body_tpl = (event_cfg.body if event_cfg and event_cfg.body else None) or _DEFAULT_BODIES[event]

    title = title_tpl.format_map(_SafeFormatDict(ctx))
    body = body_tpl.format_map(_SafeFormatDict(ctx))
    return title, body


class _SafeFormatDict(dict):
    """dict subclass that returns the placeholder itself for missing keys."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


def notify(cfg: NotificationsConfig, event: NotifyEvent, **kwargs: object) -> None:
    """Send a notification. No-op if notifications are not configured or event is disabled."""
    ap = _get_apprise(cfg)
    if ap is None:
        return

    evt_cfg = _event_config(cfg, event)
    if not evt_cfg.enabled:
        log.debug("Notification '%s' is disabled — skipping.", event.value)
        return

    title, body = _format_message(event, evt_cfg, **kwargs)
    log.info("Sending notification: %s — %s", title, body)

    try:
        ap.notify(title=title, body=body)
    except Exception:
        log.exception("Failed to send notification")


def reset() -> None:
    """Reset the module-level Apprise instance (for testing)."""
    global _apprise, _configured_urls  # noqa: PLW0603
    _apprise = None
    _configured_urls = []


