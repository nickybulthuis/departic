"""Tests for the scheduler module."""

from unittest.mock import patch

from departic.config import (
    AgendaConfig,
    EvccConfig,
    SchedulerConfig,
    Settings,
    VehicleConfig,
)
from departic.scheduler import get_status, set_enabled, start, stop


def test_get_status_returns_dict():
    status = get_status()
    assert isinstance(status, dict)
    assert "enabled" in status


def test_set_enabled_updates_status():
    set_enabled(False)
    assert get_status()["enabled"] is False
    set_enabled(True)
    assert get_status()["enabled"] is True


def test_start_no_config_uses_default():
    with (
        patch("departic.scheduler.Settings") as mock_settings,
        patch("departic.scheduler.scheduler") as mock_sched,
    ):
        mock_settings.get.return_value = None
        start()
        mock_sched.add_job.assert_called_once()
        _, kwargs = mock_sched.add_job.call_args
        assert kwargs["seconds"] == 900
        mock_sched.start.assert_called_once()


def test_start_with_config():

    cfg = Settings(
        evcc=EvccConfig(url="http://evcc.test"),
        vehicle=VehicleConfig(),
        agenda=AgendaConfig(feeds=[]),
        scheduler=SchedulerConfig(poll_interval_seconds=120),
    )
    with (
        patch("departic.scheduler.Settings") as mock_settings,
        patch("departic.scheduler.scheduler") as mock_sched,
    ):
        mock_settings.get.return_value = cfg
        start()
        _, kwargs = mock_sched.add_job.call_args
        assert kwargs["seconds"] == 120


def test_stop():
    with patch("departic.scheduler.scheduler") as mock_sched:
        stop()
        mock_sched.shutdown.assert_called_once_with(wait=False)
