from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from pydantic import BaseModel, Field


class Coords(NamedTuple):
    """Geographic coordinates (latitude, longitude)."""

    lat: float
    lon: float


class RouteResult(NamedTuple):
    """Driving route result from OSRM."""

    distance_km: float
    duration_s: float


class RouteCalculation(BaseModel):
    """Breakdown of a route-based SoC calculation."""

    one_way_km: float
    round_trip_km: float
    consumption: float
    round_trip_kwh: float
    capacity_kwh: float
    route_soc_pct: int
    duration_min: int | None = None
    duration_factor: float | None = None
    adjusted_duration_min: int | None = None
    min_soc_pct: int | None = None
    total_soc_pct: int | None = None
    trip_cost_eur: float | None = None


class TripSocResult(NamedTuple):
    """Result of a route SoC calculation for a single trip."""

    soc_pct: int
    calculation: RouteCalculation
    one_way_km: float
    duration_min: float | None


class TripEvent(BaseModel):
    """A trip event parsed from a calendar feed."""

    summary: str
    event_time: datetime
    location: str = ""
    feed_name: str = ""

    @property
    def trip_id(self) -> str:
        return str(self.event_time)


class ActiveTripState(BaseModel):
    """The currently active charging plan as persisted in state.json."""

    trip_id: str
    target_soc: int
    target_label: str = ""
    calculation: RouteCalculation | None = None
    b2b_calculation: dict | None = None
    route_km: float | None = None
    route_duration_min: float | None = None
    back_to_back: bool = False


class AppState(BaseModel):
    """Persisted application state — survives container restarts."""

    enabled: bool = True
    active_trip: ActiveTripState | None = None

    def clear_plan(self) -> AppState:
        return AppState(enabled=self.enabled)

    # ── Convenience accessors (flat interface for callers) ─────────────────

    @property
    def active_trip_id(self) -> str | None:
        return self.active_trip.trip_id if self.active_trip else None

    @property
    def active_trip_target_soc(self) -> int | None:
        return self.active_trip.target_soc if self.active_trip else None

    @property
    def active_trip_target_label(self) -> str | None:
        return self.active_trip.target_label if self.active_trip else None

    @property
    def active_trip_calculation(self) -> RouteCalculation | None:
        return self.active_trip.calculation if self.active_trip else None

    @property
    def active_trip_b2b_calculation(self) -> dict | None:
        return self.active_trip.b2b_calculation if self.active_trip else None

    @property
    def active_trip_route_km(self) -> float | None:
        return self.active_trip.route_km if self.active_trip else None

    @property
    def active_trip_route_duration_min(self) -> float | None:
        return self.active_trip.route_duration_min if self.active_trip else None

    @property
    def active_trip_back_to_back(self) -> bool:
        return self.active_trip.back_to_back if self.active_trip else False


class _TripBase(BaseModel):
    """Shared fields for UI trip representations."""

    summary: str
    event_time: str
    feed_name: str = ""
    location: str = ""
    target_soc_pct: int | None = None
    route_km: float | None = None
    route_duration_min: float | None = None
    calculation: RouteCalculation | None = None
    soc_source: str = "default"  # "default" | "routed" | "failed"
    back_to_back: bool = False
    trip_cost_eur: float | None = None


class ActivePlan(_TripBase):
    """The currently active charging plan shown in the UI."""

    b2b_calculation: dict | None = None


class UpcomingTrip(_TripBase):
    """A single trip event formatted for the UI trip list."""

    target_label: str = ""


class LiveStatus(BaseModel):
    """Full payload returned by GET /api/status."""

    enabled: bool = True
    version: str = ""
    avg_price_eur: float | None = None
    upcoming_trips: list[UpcomingTrip] = Field(default_factory=list)
    active_plan: ActivePlan | None = None
    evcc_status: EvccLiveStatus | None = None
    tick_updated: str | None = None
    tick_error: str | None = None


class EvccLiveStatus(BaseModel):
    """Live charging status snapshot fetched from EVCC at each tick."""

    vehicle_soc_pct: int | None = None
    """Current battery state of charge reported by the vehicle."""

    session_energy_kwh: float | None = None
    """Energy charged in the current session (kWh)."""

    charge_power_kw: float | None = None
    """Current charging power in kW (0 when not charging)."""

    charging_mode: str | None = None
    """Active loadpoint charging mode (e.g. 'pv', 'minpv', 'now', 'off')."""

    plan_soc_pct: int | None = None
    """Target SoC of the EVCC charging plan, if one is set."""

    plan_time: str | None = None
    """ISO timestamp the EVCC plan targets (charge-complete time), if set."""

    vehicle_connected: bool = False
    """True if a vehicle is currently connected to the loadpoint."""

    charge_remaining_kwh: float | None = None
    """Energy still needed to reach the target SoC (kWh)."""

    pv_power_kw: float | None = None
    """Current total solar generation (kW)."""

    grid_power_kw: float | None = None
    """Current grid power (kW); positive = import, negative = export."""

    tariff_grid_eur: float | None = None
    """Current grid energy price (€/kWh)."""

    solar_pct_30d: float | None = None
    """Percentage of energy charged from solar over the last 30 days."""


class VehicleInfo(BaseModel):
    """Vehicle data resolved from EVCC /api/state."""

    name: str
    title: str
    capacity_kwh: float | None = None
    min_soc_pct: int | None = None
