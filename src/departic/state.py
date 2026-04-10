"""
Departic state persistence — <DATA_DIR>/state.json

Uses atomic write (write to temp file, then rename) to prevent
state corruption if the container crashes mid-write.
"""

import logging

from pydantic import ValidationError

from departic.config import DATA_DIR
from departic.models import AppState

log = logging.getLogger(__name__)
STATE_FILE = DATA_DIR / "state.json"


def load() -> AppState:
    if STATE_FILE.exists():
        try:
            return AppState.model_validate_json(STATE_FILE.read_text())
        except (OSError, ValidationError):
            log.warning("Could not load state — starting fresh.")
    return AppState()


def save(state: AppState) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(state.model_dump_json(indent=2))
        tmp.replace(STATE_FILE)
    except OSError:
        log.exception("Could not save state")
        tmp.unlink(missing_ok=True)
