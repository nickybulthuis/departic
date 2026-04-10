"""
Routing utilities for Departic.

Uses free, open-source services — no API key required:
  - Nominatim (OpenStreetMap) for geocoding addresses to coordinates
  - OSRM (Open Source Routing Machine) for calculating route distance

Rate limiting: Nominatim requires max 1 request/second and a User-Agent.
"""

from __future__ import annotations

import logging
import time

import requests

from departic.cache import (
    GeocodeCache,
    RouteCache,
)
from departic.cache import (
    geocode_cache as _default_geocode_cache,
)
from departic.cache import (
    route_cache as _default_route_cache,
)
from departic.models import Coords, RouteCalculation, RouteResult, TripSocResult

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
USER_AGENT = "Departic (EV departure planner)"

_last_nominatim_request: float = 0.0


def _geocode_http(address: str) -> Coords | None:
    """
    Fetch coordinates from Nominatim. Respects the 1 req/s rate limit.
    Does not touch any cache — callers are responsible for caching.
    """
    global _last_nominatim_request  # noqa: PLW0603

    elapsed = time.monotonic() - _last_nominatim_request
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)

    try:
        r = requests.get(
            NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        _last_nominatim_request = time.monotonic()
        r.raise_for_status()
        results = r.json()
        if not results:
            log.warning("Nominatim: no results for %r", address)
            return None
        lat = float(results[0]["lat"])
        lon = float(results[0]["lon"])
        display = results[0].get("display_name", "")
    except Exception:
        log.exception("Geocoding failed for %r", address)
        return None
    else:
        log.info("Geocoded %r -> %s (%.4f, %.4f)", address, display, lat, lon)
        return Coords(lat, lon)


def geocode(
    address: str,
    cache: GeocodeCache | None = None,
) -> Coords | None:
    """
    Geocode an address, using the provided cache (or the module default).
    New results are saved to the cache automatically.

    If the full address fails (e.g. "Company Name\\nStreet 1, City"),
    tries each line individually in reverse order as a fallback.
    """
    gc = cache if cache is not None else _default_geocode_cache

    cached = gc.get(address)
    if cached is not None:
        log.debug("Geocache hit: %r -> (%.4f, %.4f)", address, cached[0], cached[1])
        return cached

    log.info("Geocoding (not in cache): %r", address)
    coords = _geocode_http(address)
    if coords:
        gc.set(address, coords)
        gc.save()
        return coords

    # Fallback: try each line of a multi-line address individually
    lines = [line.strip() for line in address.splitlines() if line.strip()]
    if len(lines) > 1:
        for line in reversed(lines):
            log.info("Geocoding fallback line: %r", line)
            coords = _geocode_http(line)
            if coords:
                log.info("Fallback succeeded with %r", line)
                gc.set(address, coords)
                gc.save()
                return coords

    return None


def route_distance_km(
    origin: str,
    destination: str,
    route_cache: RouteCache | None = None,
    geocode_cache: GeocodeCache | None = None,
) -> RouteResult | None:
    """
    Calculate the driving distance (km) and duration (seconds) between two
    addresses.  Uses injected caches or module-level defaults.

    Returns a ``RouteResult`` on success, or ``None`` on failure.
    ``duration_s`` may be ``None`` for legacy cache entries that pre-date
    duration tracking.
    """
    rc = route_cache if route_cache is not None else _default_route_cache

    cached = rc.get(origin, destination)
    if cached is not None:
        log.debug("Route cache hit: %r -> %r", origin, destination)
        return cached

    origin_coords = geocode(origin, cache=geocode_cache)
    if origin_coords is None:
        log.warning("Could not geocode origin: %r", origin)
        return None

    dest_coords = geocode(destination, cache=geocode_cache)
    if dest_coords is None:
        log.warning("Could not geocode destination: %r", destination)
        return None

    log.info(
        "Requesting route: (%.4f, %.4f) -> (%.4f, %.4f)",
        origin_coords.lat,
        origin_coords.lon,
        dest_coords.lat,
        dest_coords.lon,
    )

    coords = (
        f"{origin_coords.lon},{origin_coords.lat};{dest_coords.lon},{dest_coords.lat}"
    )
    try:
        r = requests.get(
            f"{OSRM_URL}/{coords}",
            params={"overview": "false"},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            log.warning("OSRM: no route found between %r and %r", origin, destination)
            return None
        route = data["routes"][0]
        distance_km = round(route["distance"] / 1000.0, 1)
        duration_s = round(route.get("duration", 0.0), 0)
    except Exception:
        log.exception("OSRM routing failed")
        return None
    else:
        log.info(
            "Route %r -> %r: %.1f km, %.0f s",
            origin,
            destination,
            distance_km,
            duration_s,
        )
        result = RouteResult(distance_km=distance_km, duration_s=duration_s)
        rc.set(origin, destination, distance_km, duration_s)
        rc.save()
        return result


def calculate_trip_soc(
    location: str,
    home_address: str,
    capacity_kwh: float,
    consumption_kwh_per_100km: float,
    route_cache: RouteCache | None = None,
    geocode_cache: GeocodeCache | None = None,
    duration_factor: float = 1.0,
) -> TripSocResult | None:
    """
    Calculate the raw route SoC for a given destination (round trip).

    Does NOT include min_soc in the returned soc_pct — callers add that.

    Returns a ``TripSocResult`` on success, or ``None`` on failure.

    ``duration_min`` is the one-way driving duration in minutes (from OSRM)
    or ``None`` when unavailable (e.g. legacy cache entry).

    ``duration_factor`` is a user-configurable multiplier applied to the OSRM
    duration to compute the adjusted driving time (charge-by deadline).
    """
    result = route_distance_km(
        home_address,
        location,
        route_cache=route_cache,
        geocode_cache=geocode_cache,
    )
    if result is None:
        return None

    if not result.distance_km:
        return None

    round_trip_km = result.distance_km * 2
    round_trip_kwh = round_trip_km * consumption_kwh_per_100km / 100.0
    route_soc = round_trip_kwh / capacity_kwh * 100.0
    soc_pct = min(100, max(1, round(route_soc)))

    duration_min = round(result.duration_s / 60.0, 0) if result.duration_s else None
    adjusted_duration_min = (
        round(duration_min * duration_factor) if duration_min is not None else None
    )

    calculation = RouteCalculation(
        one_way_km=round(result.distance_km, 1),
        round_trip_km=round(round_trip_km, 1),
        consumption=consumption_kwh_per_100km,
        round_trip_kwh=round(round_trip_kwh, 1),
        capacity_kwh=capacity_kwh,
        route_soc_pct=soc_pct,
        duration_min=int(duration_min) if duration_min is not None else None,
        duration_factor=duration_factor if duration_factor != 1.0 else None,
        adjusted_duration_min=int(adjusted_duration_min)
        if adjusted_duration_min is not None
        else None,
    )
    return TripSocResult(
        soc_pct=soc_pct,
        calculation=calculation,
        one_way_km=result.distance_km,
        duration_min=adjusted_duration_min
        if adjusted_duration_min is not None
        else duration_min,
    )


def enrich_calculation(
    calculation: RouteCalculation, min_soc_pct: int, total_soc_pct: int
) -> RouteCalculation:
    """Return a copy of the calculation with min_soc and total_soc fields set.

    Used by both the controller (active plan) and the status builder
    (upcoming trip preview) to avoid duplicating this enrichment logic.
    """
    return calculation.model_copy(
        update={"min_soc_pct": min_soc_pct, "total_soc_pct": total_soc_pct}
    )


def load_cache() -> None:
    """Load the persisted caches at startup."""
    _default_geocode_cache.load()
    _default_route_cache.load()


def precache(addresses: list[str]) -> None:
    """Geocode a list of addresses at startup, warming the cache."""
    for address in addresses:
        geocode(address)
