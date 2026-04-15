from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# In Docker the volume is mounted at /data.
# Locally, fall back to ~/.departic so files don't litter the project root.
# Override either way with DEPARTIC_DATA_DIR=<path>.
DATA_DIR = Path(
    os.environ.get(
        "DEPARTIC_DATA_DIR",
        "/data" if Path("/data").exists() else str(Path.home() / ".departic"),
    )
)
CONFIG_PATH = DATA_DIR / "departic.yaml"
MatchType = Literal["contains", "prefix"]


class EvccConfig(BaseModel):
    url: str = Field(
        description="Base URL of the EVCC instance, e.g. https://evcc.local:7070",
    )
    vehicle_title: str = Field(
        default="",
        description=(
            'Title of your vehicle as configured in EVCC (e.g. "MyCar"). '
            "Used to look up the vehicle name, capacity and SoC from /api/state."
        ),
    )
    home_address: str = Field(
        default="",
        description=(
            "Your home address used as the origin for route distance calculation. "
            'Example: "Smallville, Midwest, Freedonia"'
        ),
    )


class VehicleConfig(BaseModel):
    consumption_kwh_per_100km: float = Field(
        default=20.0,
        gt=0,
        description=(
            "Average energy consumption in kWh per 100 km. "
            "Used to convert route distance to a SoC target. "
        ),
    )
    back_to_back_window_hours: float = Field(
        default=24.0,
        gt=0,
        description=(
            "Time window (in hours) used to determine whether trips are "
            "considered back-to-back. If multiple trips depart within this "
            "window relative to the first trip, their SoC targets are combined "
            "and a single accumulated target is applied to all of them."
        ),
    )
    route_duration_factor: float = Field(
        default=1.0,
        gt=0,
        description=(
            "Multiplier applied to the OSRM route duration before computing the "
            "charge-by time. Values > 1.0 add a buffer (e.g. 1.2 = 20%% extra "
            "driving time assumed); values < 1.0 reduce it. Default: 1.0 (no "
            "adjustment)."
        ),
    )


class EventMappingEntry(BaseModel):
    tag: str = Field(
        description="Text to search for in the event name or description.",
    )
    match: MatchType = Field(
        default="contains",
        description=(
            "How the tag is matched. "
            "'contains': tag appears anywhere (case-insensitive). "
            "'prefix': event name starts with the tag."
        ),
    )


class FeedConfig(BaseModel):
    url: str = Field(description="iCal feed URL (must be publicly readable).")
    name: str = Field(
        default="",
        description='Optional display name for this calendar (e.g. "Cindy").',
    )


class AgendaConfig(BaseModel):
    feeds: list[FeedConfig] = Field(
        default_factory=list,
        description=(
            "List of iCal feeds to monitor. All trip events from all feeds are "
            "combined and sorted by departure time."
        ),
    )
    trip_mapping: list[EventMappingEntry] = Field(
        default_factory=list,
        description=("Tags that mark events as trip departure events."),
    )
    lookahead_days: int = Field(
        default=14,
        ge=1,
        le=365,
        description=(
            "How many days ahead to include upcoming trips. Default: 14 days."
        ),
    )

    def is_configured(self) -> bool:
        return bool(self.feeds)


class SchedulerConfig(BaseModel):
    poll_interval_seconds: int = Field(
        default=900,
        ge=60,
        le=3600,
        description="How often (in seconds) the calendar is checked. "
        "Default: 900s (15 min).",
    )


class EventNotificationConfig(BaseModel):
    enabled: bool = Field(
        default=True,
        description="Whether this event type triggers a notification.",
    )
    title: str = Field(
        default="",
        description=(
            "Custom title template. Supports {variable} placeholders. "
            "Leave empty to use the built-in default."
        ),
    )
    body: str = Field(
        default="",
        description=(
            "Custom body template. Supports {variable} placeholders. "
            "Leave empty to use the built-in default."
        ),
    )


class EventsConfig(BaseModel):
    plan_activated: EventNotificationConfig = Field(
        default_factory=EventNotificationConfig,
        description=(
            "New charging plan set. "
            "Variables: {summary}, {soc_pct}, {deadline}, {route_km}"
        ),
    )
    plan_updated: EventNotificationConfig = Field(
        default_factory=EventNotificationConfig,
        description=(
            "Existing plan changed. Variables: {summary}, {old_soc_pct}, {new_soc_pct}"
        ),
    )
    plan_cleared: EventNotificationConfig = Field(
        default_factory=EventNotificationConfig,
        description=("Charging plan removed. Variables: {summary}"),
    )
    routing_failed: EventNotificationConfig = Field(
        default_factory=EventNotificationConfig,
        description=(
            "Route calculation failed, fell back to 100%. "
            "Variables: {summary}, {location}"
        ),
    )
    toggled: EventNotificationConfig = Field(
        default_factory=EventNotificationConfig,
        description=("Departic enabled or disabled. Variables: {enabled}, {label}"),
    )


class NotificationsConfig(BaseModel):
    urls: list[str] = Field(
        default_factory=list,
        description=(
            "List of Apprise notification URLs. "
            "Supports 80+ services: Telegram, Slack, Discord, "
            "Pushover, ntfy, email, etc. "
            "See https://github.com/caronc/apprise/wiki for "
            "all supported services."
        ),
    )
    events: EventsConfig = Field(
        default_factory=EventsConfig,
        description=(
            "Per-event notification settings (enable/disable, custom messages)."
        ),
    )

    def is_configured(self) -> bool:
        return bool(self.urls)


class Settings(BaseModel):
    evcc: EvccConfig
    vehicle: VehicleConfig = Field(default_factory=VehicleConfig)
    agenda: AgendaConfig
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)

    def is_configured(self) -> bool:
        return bool(self.evcc.url and self.agenda.feeds)

    @classmethod
    def get(cls) -> Settings | None:
        """
        Return the current settings, loading from disk if needed.
        Returns None if the config file is missing or invalid.
        Re-reads only when the file mtime has changed.
        """
        from departic.cache import settings_cache  # noqa: PLC0415, I001 — circular import guard

        return settings_cache.get()

    @classmethod
    def reload(cls) -> Settings | None:
        """Force reload settings from disk, ignoring the mtime cache."""
        from departic.cache import settings_cache  # noqa: PLC0415, I001 — circular import guard

        settings_cache.invalidate()
        return cls.get()


# Module-level convenience — used at startup
settings: Settings | None = Settings.get()
