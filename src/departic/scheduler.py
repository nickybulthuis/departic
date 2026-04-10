from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Final

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from departic import __version__
from departic.config import Settings
from departic.models import LiveStatus
from departic.tick import run_tick

log = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS: Final[int] = 900

scheduler = AsyncIOScheduler()
_live_status = LiveStatus(version=__version__)
_current_interval: int = DEFAULT_POLL_INTERVAL_SECONDS


def get_status() -> dict[str, object]:
    return _live_status.model_dump()


def set_enabled(enabled: bool) -> None:
    """Update enabled state immediately in live status without waiting for next tick."""
    global _live_status  # noqa: PLW0603
    _live_status = _live_status.model_copy(update={"enabled": enabled})


def _check_interval() -> None:
    """Re-read the poll interval from config and reschedule if it changed."""
    global _current_interval  # noqa: PLW0603
    cfg = Settings.get()
    new_interval = (
        cfg.scheduler.poll_interval_seconds
        if cfg is not None
        else DEFAULT_POLL_INTERVAL_SECONDS
    )
    if new_interval != _current_interval:
        log.info(
            "Poll interval changed: %ds → %ds — rescheduling.",
            _current_interval,
            new_interval,
        )
        _current_interval = new_interval
        scheduler.reschedule_job(
            "departic_tick", trigger="interval", seconds=new_interval
        )


async def _tick() -> None:
    global _live_status  # noqa: PLW0603
    _check_interval()
    _live_status = run_tick()


def start() -> None:
    global _current_interval  # noqa: PLW0603
    cfg = Settings.get()
    _current_interval = (
        cfg.scheduler.poll_interval_seconds
        if cfg is not None
        else DEFAULT_POLL_INTERVAL_SECONDS
    )
    scheduler.add_job(
        _tick,
        trigger="interval",
        seconds=_current_interval,
        id="departic_tick",
        replace_existing=True,
        next_run_time=datetime.now(UTC),
    )
    scheduler.start()
    log.info("Scheduler started — cycle: %ds.", _current_interval)


def stop() -> None:
    scheduler.shutdown(wait=False)
