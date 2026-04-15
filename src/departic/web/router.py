from __future__ import annotations

import contextlib
import hashlib
import logging
import os
from datetime import UTC, datetime, timedelta
from math import floor
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from departic import __version__, scheduler
from departic import state as state_store
from departic.config import CONFIG_PATH, Settings
from departic.evcc import EvccAPI
from departic.notifier import NotifyEvent, notify

ROOT_PATH = os.environ.get("ROOT_PATH", "")

log = logging.getLogger(__name__)
router = APIRouter()

_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

_STATIC_DIR = Path(__file__).parent / "static"


def _static_hash() -> str:
    """Return a short hash of all static files for cache-busting."""
    h = hashlib.md5()  # noqa: S324 — not used for security
    for f in sorted(_STATIC_DIR.rglob("*")):
        if f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()[:8]


_STATIC_V = _static_hash()

# ── Jinja helpers ──────────────────────────────────────────────────────────

FEED_COLORS = [
    "is-info",
    "is-success",
    "is-warning",
    "is-danger",
    "is-primary",
    "is-link",
]

SOC_COLOR_MAP = {
    "routed": "is-info",
    "routing_failed": "is-danger",
    "default": "is-warning",
}


def _feed_color(name: str) -> str:
    if not name:
        return ""
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) & 0xFFFF
    return FEED_COLORS[h % len(FEED_COLORS)]


def _fmt_dow(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%a")


def _fmt_date(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%-d %b")


def _fmt_time(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    now = datetime.now(dt.tzinfo)
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    if timedelta(0) < dt - now < timedelta(days=7):
        return dt.strftime("%a %H:%M")
    return dt.strftime("%d-%m %H:%M")


def _fmt_countdown(iso: str) -> str:
    diff = (datetime.fromisoformat(iso) - datetime.now(UTC)).total_seconds()
    if diff < 0:
        return "departed"
    h = floor(diff / 3600)
    m = floor((diff % 3600) / 60)
    if h < 1:
        return f"{m}m"
    if h < 24:
        return f"{h}h {m}m"
    return f"{floor(h / 24)}d {h % 24}h"


def _is_soon(iso: str) -> bool:
    diff = (datetime.fromisoformat(iso) - datetime.now(UTC)).total_seconds()
    return 0 < diff < 4 * 3600


def _is_departed(iso: str) -> bool:
    return (datetime.fromisoformat(iso) - datetime.now(UTC)).total_seconds() < 0


def _fmt_eur(value: float | None) -> str:
    if value is None:
        return ""
    return f"€{value:.2f}"


def _fmt_duration(minutes: float | None) -> str:
    """Format a duration in minutes as e.g. '1h 23m' or '45 min'."""
    if minutes is None:
        return ""
    m = int(minutes)
    if m < 60:
        return f"{m} min"
    h, rem = divmod(m, 60)
    return f"{h}h {rem:02d}m" if rem else f"{h}h"


def _fmt_departure(event_time_iso: str, duration_min: float | None) -> str:
    """Format the estimated departure time (event start minus driving duration)."""
    if duration_min is None:
        return ""
    event = datetime.fromisoformat(event_time_iso)
    departure = event - timedelta(minutes=duration_min)
    now = datetime.now(departure.tzinfo)
    if departure.date() == now.date():
        return departure.strftime("%H:%M")
    if timedelta(0) < departure - now < timedelta(days=7):
        return departure.strftime("%a %H:%M")
    return departure.strftime("%d-%m %H:%M")


def _fmt_factor(value: float | None) -> str:
    """Format a duration factor, e.g. 1.2 -> 'x1.2'."""
    if value is None:
        return ""
    return f"\u00d7{value:.2g}"


def _fmt_power(kw: float | None) -> str:
    """Format a kW value as W (below 1 kW) or kW (1 kW and above).

    Examples:
        0.428  -> '428 W'
        0.9999 -> '999 W'
        1.0    -> '1.0 kW'
        10.4   -> '10.4 kW'
    """
    if kw is None:
        return "-"
    w = kw * 1000
    if w < 1000:
        return f"{round(w)}\u2009W"
    return f"{round(kw, 1)}\u2009kW"


# Register Jinja filters
templates.env.filters["feed_color"] = _feed_color
templates.env.filters["fmt_dow"] = _fmt_dow
templates.env.filters["fmt_date"] = _fmt_date
templates.env.filters["fmt_time"] = _fmt_time
templates.env.filters["fmt_countdown"] = _fmt_countdown
templates.env.filters["is_soon"] = _is_soon
templates.env.filters["is_departed"] = _is_departed
templates.env.filters["soc_color"] = lambda src: SOC_COLOR_MAP.get(src, "is-warning")
templates.env.filters["fmt_eur"] = _fmt_eur
templates.env.filters["fmt_duration"] = _fmt_duration
templates.env.filters["fmt_departure"] = _fmt_departure
templates.env.filters["fmt_factor"] = _fmt_factor
templates.env.filters["fmt_power"] = _fmt_power


# ── Routes ─────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    cfg = Settings.get()
    configured = cfg is not None and cfg.is_configured()

    status: dict = scheduler.get_status() if configured else {}

    # Determine EVCC polling interval for the live JS updater
    evcc_poll_interval = 30
    if configured and cfg and cfg.evcc:
        with contextlib.suppress(Exception):
            evcc_poll_interval = EvccAPI(cfg.evcc.url).get_interval()

    # Page reload interval — matches the calendar tick (default 15 min)
    page_reload_interval = (
        cfg.scheduler.poll_interval_seconds if (configured and cfg) else 900
    )

    current_log_level = logging.getLevelName(
        logging.getLogger("departic").level or logging.getLogger().level
    ).lower()

    evcc_url = cfg.evcc.url if (configured and cfg) else None

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "configured": configured,
            "config_path": str(CONFIG_PATH),
            "root_path": ROOT_PATH,
            "static_v": _STATIC_V,
            "status": status,
            "current_log_level": current_log_level,
            "version": status.get("version", __version__),
            "evcc_poll_interval": evcc_poll_interval,
            "page_reload_interval": page_reload_interval,
            "evcc_url": evcc_url,
        },
    )


@router.get("/api/status")
async def api_status():
    return JSONResponse(scheduler.get_status())


@router.post("/api/trigger")
async def api_trigger():
    try:
        scheduler.scheduler.modify_job("departic_tick", next_run_time=datetime.now(UTC))
    except Exception:
        log.exception("Failed to trigger cycle")
    return RedirectResponse(url=f"{ROOT_PATH}/", status_code=303)


@router.post("/api/toggle")
async def api_toggle():
    current = state_store.load()
    updated = current.model_copy(update={"enabled": not current.enabled})
    state_store.save(updated)
    scheduler.set_enabled(updated.enabled)

    cfg = Settings.get()
    if cfg:
        notify(cfg.notifications, NotifyEvent.TOGGLED, enabled=updated.enabled)

    return RedirectResponse(url=f"{ROOT_PATH}/", status_code=303)


@router.post("/api/loglevel")
async def set_log_level(level: str = Form(...)):
    levels = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    level_value = levels.get(level.lower())
    if level_value is None:
        return JSONResponse({"ok": False, "error": "Invalid level"}, status_code=400)

    # Set on root logger and all departic.* loggers explicitly
    logging.getLogger().setLevel(level_value)
    for name, logger in logging.Logger.manager.loggerDict.items():
        if name.startswith("departic") and isinstance(logger, logging.Logger):
            logger.setLevel(level_value)

    log.info("Log level changed to %s", level.upper())
    return RedirectResponse(url=f"{ROOT_PATH}/", status_code=303)


@router.get("/api/loglevel")
async def get_log_level():
    level = logging.getLogger("departic").level or logging.getLogger().level
    return JSONResponse({"level": logging.getLevelName(level).lower()})


@router.get("/api/evcc")
async def api_evcc():
    """Return live EVCC status as JSON, fetched directly from EVCC (not cached)."""
    cfg = Settings.get()
    if cfg is None or not cfg.evcc or not cfg.evcc.url:
        return JSONResponse({"error": "not configured"}, status_code=404)
    api = EvccAPI(cfg.evcc.url)
    status = api.get_loadpoint_status(cfg.evcc.vehicle_title or "")
    if status is None:
        return JSONResponse({"error": "no loadpoint"}, status_code=503)
    interval = api.get_interval()
    return JSONResponse({**status.model_dump(), "interval": interval})


@router.get("/api/health")
async def health():
    return JSONResponse({"status": "ok"})
