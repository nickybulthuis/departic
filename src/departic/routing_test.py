"""
Tests for Nominatim geocoding, OSRM routing, and SoC calculation.
All tests use injected caches — no module-level state is touched.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import responses as rsps_lib

from departic.cache import GeocodeCache, RouteCache
from departic.models import Coords
from departic.routing import (
    NOMINATIM_URL,
    OSRM_URL,
    calculate_trip_soc,
    geocode,
    load_cache,
    precache,
    route_distance_km,
)


@pytest.fixture
def gc() -> GeocodeCache:
    """Fresh in-memory geocode cache (no disk I/O)."""
    cache = GeocodeCache(cache_file=Path("/dev/null"))
    cache._file = Path("/dev/null")
    return cache


@pytest.fixture
def rc() -> RouteCache:
    return RouteCache()


@rsps_lib.activate
def test_geocode_success(gc):
    rsps_lib.add(
        rsps_lib.GET,
        NOMINATIM_URL,
        json=[
            {
                "lat": "10.1234",
                "lon": "20.5678",
                "display_name": "Smallville, Midwest, Freedonia",
            }
        ],
    )
    coords = geocode("Smallville, Midwest, Freedonia", cache=gc)
    assert coords == pytest.approx((10.1234, 20.5678), rel=1e-4)
    assert gc.get("Smallville, Midwest, Freedonia") is not None


@rsps_lib.activate
def test_geocode_cached(gc):
    gc.set("Metropolis", Coords(10.123, 20.456))
    coords = geocode("Metropolis", cache=gc)
    assert coords == (10.123, 20.456)
    assert len(rsps_lib.calls) == 0


@rsps_lib.activate
def test_geocode_no_results(gc):
    rsps_lib.add(rsps_lib.GET, NOMINATIM_URL, json=[])
    coords = geocode("xyzzy-does-not-exist", cache=gc)
    assert coords is None


@rsps_lib.activate
def test_geocode_multiline_fallback(gc):
    """Falls back to last line of a multi-line address."""
    rsps_lib.add(rsps_lib.GET, NOMINATIM_URL, json=[])  # full string fails
    rsps_lib.add(rsps_lib.GET, NOMINATIM_URL, json=[])  # first line fails
    rsps_lib.add(
        rsps_lib.GET,
        NOMINATIM_URL,
        json=[{"lat": "10.1234", "lon": "20.5678", "display_name": "Smallville"}],
    )
    coords = geocode("Company Name\nStreet 1\nSmallville", cache=gc)
    assert coords is not None
    # Cached under the original full address
    assert gc.get("Company Name\nStreet 1\nSmallville") is not None


@rsps_lib.activate
def test_route_distance_km(gc, rc):
    gc.set("Smallville", Coords(10.123, 20.456))
    gc.set("Gotham", Coords(11.234, 21.567))
    rsps_lib.add(
        rsps_lib.GET,
        f"{OSRM_URL}/20.456,10.123;21.567,11.234",
        json={"code": "Ok", "routes": [{"distance": 34900.0, "duration": 1800.0}]},
    )
    result = route_distance_km("Smallville", "Gotham", route_cache=rc, geocode_cache=gc)
    assert result is not None
    km, duration_s = result
    assert km == pytest.approx(34.9, rel=1e-2)
    assert duration_s == 1800.0
    cached = rc.get("Smallville", "Gotham")
    assert cached is not None
    assert cached[0] == pytest.approx(34.9, rel=1e-2)
    assert cached[1] == 1800.0


@rsps_lib.activate
def test_route_distance_cached(gc, rc):
    gc.set("A", Coords(1.0, 2.0))
    gc.set("B", Coords(3.0, 4.0))
    rc.set("A", "B", 42.0, 2520.0)
    result = route_distance_km("A", "B", route_cache=rc, geocode_cache=gc)
    assert result is not None
    assert result.distance_km == 42.0
    assert result.duration_s == 2520.0
    assert len(rsps_lib.calls) == 0


@rsps_lib.activate
def test_route_no_result(gc, rc):
    gc.set("A", Coords(1.0, 2.0))
    gc.set("B", Coords(3.0, 4.0))
    rsps_lib.add(
        rsps_lib.GET,
        f"{OSRM_URL}/2.0,1.0;4.0,3.0",
        json={"code": "NoRoute", "routes": []},
    )
    result = route_distance_km("A", "B", route_cache=rc, geocode_cache=gc)
    assert result is None


def test_calculate_trip_soc(gc, rc):
    gc.set("Home", Coords(10.123, 20.456))
    gc.set("Gotham", Coords(11.234, 21.567))
    rc.set("Home", "Gotham", 35.0, 1800.0)

    soc, calculation, one_way_km, duration_min = calculate_trip_soc(
        location="Gotham",
        home_address="Home",
        capacity_kwh=64.0,
        consumption_kwh_per_100km=20.0,
        route_cache=rc,
        geocode_cache=gc,
    )
    # Route  35 km x 2 = 70 km
    # Energy 70 x 20.0 kWh/100km = 14.0 kWh
    # SoC    14.0 / 64 kWh = 22%
    assert soc == 22
    assert one_way_km == 35.0
    assert duration_min == 30.0
    assert calculation.one_way_km == 35.0
    assert calculation.round_trip_km == 70.0
    assert calculation.round_trip_kwh == 14.0
    assert calculation.route_soc_pct == 22
    assert calculation.duration_min == 30
    assert calculation.min_soc_pct is None


def test_calculate_trip_soc_caps_at_100(gc, rc):
    gc.set("Home", Coords(10.123, 20.456))
    gc.set("FarAway", Coords(48.0, 2.0))
    rc.set("Home", "FarAway", 500.0, 18000.0)

    soc, _, _, _ = calculate_trip_soc(
        location="FarAway",
        home_address="Home",
        capacity_kwh=64.0,
        consumption_kwh_per_100km=20.0,
        route_cache=rc,
        geocode_cache=gc,
    )
    assert soc == 100


@rsps_lib.activate
def test_geocode_http_exception(gc):
    """_geocode_http returns None on unexpected exception."""
    rsps_lib.add(rsps_lib.GET, NOMINATIM_URL, body=Exception("network failure"))
    coords = geocode("Anywhere", cache=gc)
    assert coords is None


@rsps_lib.activate
def test_route_distance_origin_not_geocodable(gc, rc):
    """route_distance_km returns None when origin cannot be geocoded."""
    rsps_lib.add(rsps_lib.GET, NOMINATIM_URL, json=[])  # origin fails
    result = route_distance_km(
        "Unknown Origin", "Gotham", route_cache=rc, geocode_cache=gc
    )
    assert result is None


@rsps_lib.activate
def test_route_distance_dest_not_geocodable(gc, rc):
    """route_distance_km returns None when destination cannot be geocoded."""
    gc.set("Home", Coords(10.0, 20.0))
    rsps_lib.add(rsps_lib.GET, NOMINATIM_URL, json=[])  # dest fails
    result = route_distance_km("Home", "Unknown Dest", route_cache=rc, geocode_cache=gc)
    assert result is None


@rsps_lib.activate
def test_route_distance_osrm_exception(gc, rc):
    """route_distance_km returns None on OSRM network error."""
    gc.set("A", Coords(1.0, 2.0))
    gc.set("B", Coords(3.0, 4.0))
    rsps_lib.add(rsps_lib.GET, f"{OSRM_URL}/2.0,1.0;4.0,3.0", body=Exception("timeout"))
    result = route_distance_km("A", "B", route_cache=rc, geocode_cache=gc)
    assert result is None


def test_calculate_trip_soc_with_duration_factor(gc, rc):
    gc.set("Home", Coords(10.123, 20.456))
    gc.set("Gotham", Coords(11.234, 21.567))
    rc.set("Home", "Gotham", 35.0, 1800.0)  # 30 min raw

    result = calculate_trip_soc(
        location="Gotham",
        home_address="Home",
        capacity_kwh=64.0,
        consumption_kwh_per_100km=20.0,
        route_cache=rc,
        geocode_cache=gc,
        duration_factor=1.2,
    )
    assert result is not None
    # Raw 30 min x 1.2 = 36 min adjusted; duration_min returned is the adjusted value
    assert result.duration_min == 36.0
    assert result.calculation.duration_min == 30  # raw OSRM value stored
    assert result.calculation.duration_factor == 1.2
    assert result.calculation.adjusted_duration_min == 36


def test_calculate_trip_soc_factor_none_when_default(gc, rc):
    gc.set("Home", Coords(10.123, 20.456))
    gc.set("Gotham", Coords(11.234, 21.567))
    rc.set("Home", "Gotham", 35.0, 1800.0)

    result = calculate_trip_soc(
        location="Gotham",
        home_address="Home",
        capacity_kwh=64.0,
        consumption_kwh_per_100km=20.0,
        route_cache=rc,
        geocode_cache=gc,
        # default duration_factor=1.0
    )
    assert result is not None
    assert (
        result.calculation.duration_factor is None
    )  # stored as None when factor == 1.0


def test_load_cache_and_precache(tmp_path):
    """load_cache() and precache() run without error."""

    with patch("departic.routing._default_geocode_cache") as mock_cache:
        mock_cache.get.return_value = (1.0, 2.0)
        load_cache()
        precache(["Metropolis", "Gotham"])
