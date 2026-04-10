"""
Tests for the EVCC API client.
"""

import re
from datetime import UTC, datetime, timedelta, timezone

import responses as rsps_lib

from departic.evcc import EvccAPI


def make_evcc(evcc_url) -> EvccAPI:
    return EvccAPI(evcc_url)


@rsps_lib.activate
def test_get_vehicle_found(evcc_url, evcc_state):
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=evcc_state)
    vehicle = make_evcc(evcc_url).get_vehicle("MyCar")
    assert vehicle is not None
    assert vehicle.name == "mycar"
    assert vehicle.title == "MyCar"
    assert vehicle.capacity_kwh == 64.0
    assert vehicle.min_soc_pct == 20


@rsps_lib.activate
def test_get_vehicle_not_found(evcc_url, evcc_state):
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=evcc_state)
    vehicle = make_evcc(evcc_url).get_vehicle("Polestar")
    assert vehicle is None


@rsps_lib.activate
def test_get_vehicle_no_capacity(evcc_url):
    """Vehicle without capacity returns None for capacity_kwh."""
    state = {
        "vehicles": {"mycar": {"title": "MyCar"}},
        "loadpoints": [],
    }
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=state)
    vehicle = make_evcc(evcc_url).get_vehicle("MyCar")
    assert vehicle is not None
    assert vehicle.capacity_kwh is None
    assert vehicle.min_soc_pct is None


@rsps_lib.activate
def test_set_plan_soc(evcc_url):
    departure = datetime(2026, 4, 5, 8, 0, tzinfo=UTC)
    rsps_lib.add(
        rsps_lib.POST,
        re.compile(rf"{re.escape(evcc_url)}/api/vehicles/mycar/plan/soc/80/"),
        json={"result": {}},
    )
    make_evcc(evcc_url).set_plan_soc("mycar", 80, departure)
    assert len(rsps_lib.calls) == 1
    assert "/vehicles/mycar/plan/soc/80/" in rsps_lib.calls[0].request.url


@rsps_lib.activate
def test_set_plan_soc_encodes_plus_in_timezone_offset(evcc_url):
    """The + in a timezone offset must be percent-encoded as %2B in the URL."""
    tz_plus2 = timezone(timedelta(hours=2))
    departure = datetime(2026, 4, 6, 8, 15, 0, tzinfo=tz_plus2)
    rsps_lib.add(
        rsps_lib.POST,
        re.compile(rf"{re.escape(evcc_url)}/api/vehicles/mycar/plan/soc/74/"),
        json={"result": {}},
    )
    make_evcc(evcc_url).set_plan_soc("mycar", 74, departure)
    assert len(rsps_lib.calls) == 1
    assert "%2B" in rsps_lib.calls[0].request.url
    assert "+" not in rsps_lib.calls[0].request.url.split("?")[0]


@rsps_lib.activate
def test_set_plan_soc_vehicle_name_with_colon(evcc_url):
    """Vehicle names like 'db:6' must keep the colon unencoded in the URL path."""
    departure = datetime(2026, 4, 6, 8, 0, tzinfo=UTC)
    rsps_lib.add(
        rsps_lib.POST,
        re.compile(rf"{re.escape(evcc_url)}/api/vehicles/db:6/plan/soc/74/"),
        json={"result": {}},
    )
    make_evcc(evcc_url).set_plan_soc("db:6", 74, departure)
    assert len(rsps_lib.calls) == 1
    assert "/vehicles/db:6/plan/soc/" in rsps_lib.calls[0].request.url
    assert "%3A" not in rsps_lib.calls[0].request.url


@rsps_lib.activate
def test_has_plan_soc_vehicle_name_with_colon(evcc_url):
    """has_plan_soc must keep the colon unencoded in the vehicle name path segment."""
    rsps_lib.add(
        rsps_lib.GET,
        f"{evcc_url}/api/vehicles/db:6/plan/soc",
        json={"result": {"soc": 80, "time": "2026-04-06T08:00:00Z"}},
    )
    assert make_evcc(evcc_url).has_plan_soc("db:6") is True
    assert "/vehicles/db:6/plan/soc" in rsps_lib.calls[0].request.url
    assert "%3A" not in rsps_lib.calls[0].request.url


@rsps_lib.activate
def test_delete_plan_soc_vehicle_name_with_colon(evcc_url):
    """delete_plan_soc must keep the colon unencoded in the vehicle name."""
    rsps_lib.add(
        rsps_lib.DELETE,
        f"{evcc_url}/api/vehicles/db:6/plan/soc",
        json={"result": {}},
    )
    make_evcc(evcc_url).delete_plan_soc("db:6")
    assert len(rsps_lib.calls) == 1
    assert "/vehicles/db:6/plan/soc" in rsps_lib.calls[0].request.url
    assert "%3A" not in rsps_lib.calls[0].request.url


@rsps_lib.activate
def test_has_plan_soc_true(evcc_url):
    rsps_lib.add(
        rsps_lib.GET,
        f"{evcc_url}/api/vehicles/mycar/plan/soc",
        json={"result": {"soc": 80, "time": "2026-04-05T08:00:00Z"}},
    )
    assert make_evcc(evcc_url).has_plan_soc("mycar") is True


@rsps_lib.activate
def test_has_plan_soc_false(evcc_url):
    rsps_lib.add(
        rsps_lib.GET,
        f"{evcc_url}/api/vehicles/mycar/plan/soc",
        json={"result": {"soc": 0, "time": "0001-01-01T00:00:00Z"}},
    )
    assert make_evcc(evcc_url).has_plan_soc("mycar") is False


@rsps_lib.activate
def test_has_plan_soc_404(evcc_url):
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/vehicles/mycar/plan/soc", status=404)
    assert make_evcc(evcc_url).has_plan_soc("mycar") is False


@rsps_lib.activate
def test_delete_plan_soc(evcc_url):
    rsps_lib.add(
        rsps_lib.DELETE,
        f"{evcc_url}/api/vehicles/mycar/plan/soc",
        json={"result": {}},
    )
    make_evcc(evcc_url).delete_plan_soc("mycar")
    assert len(rsps_lib.calls) == 1


# ── get_avg_price ─────────────────────────────────────────────────────────


@rsps_lib.activate
def test_get_avg_price_returns_value(evcc_url):
    state = {
        "vehicles": {},
        "statistics": {"30d": {"avgPrice": 0.28}},
    }
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=state)
    assert make_evcc(evcc_url).get_avg_price() == 0.28


@rsps_lib.activate
def test_get_avg_price_missing_statistics(evcc_url):
    state = {"vehicles": {}}
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=state)
    assert make_evcc(evcc_url).get_avg_price() is None


@rsps_lib.activate
def test_get_avg_price_missing_30d(evcc_url):
    state = {"vehicles": {}, "statistics": {}}
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=state)
    assert make_evcc(evcc_url).get_avg_price() is None


@rsps_lib.activate
def test_get_avg_price_null_value(evcc_url):
    state = {"vehicles": {}, "statistics": {"30d": {"avgPrice": None}}}
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=state)
    assert make_evcc(evcc_url).get_avg_price() is None


# ── get_loadpoint_status ───────────────────────────────────────────────────


def _lp_state(loadpoints: list[dict], vehicles: dict | None = None) -> dict:
    return {"vehicles": vehicles or {}, "loadpoints": loadpoints}


@rsps_lib.activate
def test_get_loadpoint_status_connected_vehicle(evcc_url):
    state = _lp_state(
        loadpoints=[
            {
                "vehicleTitle": "MyCar",
                "vehicleName": "mycar",
                "vehicleSoc": 55,
                "chargePower": 7400,
                "chargedEnergy": 18000,
                "mode": "pv",
                "connected": True,
            }
        ],
        vehicles={"mycar": {"plan": {"soc": 80, "time": "2026-04-11T07:00:00+02:00"}}},
    )
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=state)
    result = make_evcc(evcc_url).get_loadpoint_status("MyCar")
    assert result is not None
    assert result.vehicle_soc_pct == 55
    assert result.charge_power_kw == 7.4
    assert result.session_energy_kwh == 18.0
    assert result.charging_mode == "pv"
    assert result.vehicle_connected is True
    assert result.plan_soc_pct == 80
    assert result.plan_time == "2026-04-11T07:00:00+02:00"


@rsps_lib.activate
def test_get_loadpoint_status_no_plan(evcc_url):
    state = _lp_state(
        [
            {
                "vehicleTitle": "MyCar",
                "vehicleName": "mycar",
                "vehicleSoc": 0,
                "chargePower": 0,
                "chargedEnergy": 0,
                "mode": "off",
                "connected": False,
            }
        ]
    )
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=state)
    result = make_evcc(evcc_url).get_loadpoint_status("MyCar")
    assert result is not None
    assert result.plan_soc_pct is None
    assert result.plan_time is None
    assert result.charge_power_kw == 0.0
    assert result.session_energy_kwh == 0.0
    assert result.vehicle_connected is False
    assert result.vehicle_soc_pct is None


@rsps_lib.activate
def test_get_loadpoint_status_falls_back_to_first_loadpoint(evcc_url):
    """When no loadpoint matches the vehicle title, use the first one."""
    state = _lp_state(
        [
            {
                "vehicleTitle": "OtherCar",
                "vehicleName": "othercar",
                "vehicleSoc": 30,
                "chargePower": 0,
                "chargedEnergy": 0,
                "mode": "off",
                "connected": False,
            },
        ]
    )
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=state)
    result = make_evcc(evcc_url).get_loadpoint_status("MyCar")
    assert result is not None
    assert result.vehicle_soc_pct == 30


@rsps_lib.activate
def test_get_loadpoint_status_no_loadpoints(evcc_url):
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json=_lp_state([]))
    assert make_evcc(evcc_url).get_loadpoint_status("MyCar") is None


@rsps_lib.activate
def test_get_loadpoint_status_request_error(evcc_url):
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", body=Exception("down"))
    assert make_evcc(evcc_url).get_loadpoint_status("MyCar") is None


# ── get_interval ───────────────────────────────────────────────────────────


@rsps_lib.activate
def test_get_interval_returns_value(evcc_url):
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", json={"interval": 30})
    assert make_evcc(evcc_url).get_interval() == 30


@rsps_lib.activate
def test_get_interval_default_on_error(evcc_url):
    rsps_lib.add(rsps_lib.GET, f"{evcc_url}/api/state", body=Exception("down"))
    assert make_evcc(evcc_url).get_interval() == 30
