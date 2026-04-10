"""
Shared pytest fixtures for Departic tests.
"""

import pytest

from departic.config import AgendaConfig, EvccConfig, Settings, VehicleConfig


@pytest.fixture
def evcc_url() -> str:
    return "http://evcc.test"


@pytest.fixture
def cfg(evcc_url) -> Settings:
    return Settings(
        evcc=EvccConfig(
            url=evcc_url,
            vehicle_title="MyCar",
            home_address="Smallville, Midwest, Freedonia",
        ),
        vehicle=VehicleConfig(consumption_kwh_per_100km=20.0),
        agenda=AgendaConfig(feeds=[]),
    )


@pytest.fixture
def evcc_state() -> dict:
    """A minimal but realistic EVCC /api/state response."""
    return {
        "vehicles": {
            "mycar": {
                "title": "MyCar",
                "capacity": 64.0,
                "minSoc": 20,
            }
        },
        "loadpoints": [],
    }
