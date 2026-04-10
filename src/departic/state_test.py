"""Tests for state persistence."""

from unittest.mock import patch

from departic.models import ActiveTripState, AppState
from departic.state import load, save


def test_load_returns_default_when_no_file(tmp_path):
    with patch("departic.state.STATE_FILE", tmp_path / "state.json"):
        state = load()
    assert state.enabled is True
    assert state.active_trip is None


def test_save_and_load_roundtrip(tmp_path):
    state_file = tmp_path / "state.json"
    state = AppState(
        enabled=False,
        active_trip=ActiveTripState(trip_id="test-id", target_soc=80),
    )
    with patch("departic.state.STATE_FILE", state_file):
        save(state)
        loaded = load()
    assert loaded.enabled is False
    assert loaded.active_trip_id == "test-id"
    assert loaded.active_trip_target_soc == 80


def test_load_returns_default_on_corrupt_file(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text("not valid json")
    with patch("departic.state.STATE_FILE", state_file):
        state = load()
    assert state.enabled is True
    assert state.active_trip is None


def test_save_creates_parent_dirs(tmp_path):
    state_file = tmp_path / "sub" / "state.json"
    state = AppState(enabled=True)
    with patch("departic.state.STATE_FILE", state_file):
        save(state)
    assert state_file.exists()


def test_save_os_error_does_not_raise(tmp_path):
    """save() logs the error and does not raise when writing fails."""
    # Make the directory read-only so writing the tmp file fails naturally
    state_file = tmp_path / "state.json"
    state = AppState(enabled=True)
    tmp_path.chmod(0o555)  # read + execute, no write
    try:
        with patch("departic.state.STATE_FILE", state_file):
            save(state)  # must not raise
    finally:
        tmp_path.chmod(0o755)  # restore so pytest can clean up
