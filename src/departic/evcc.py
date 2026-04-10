"""
EVCC REST API client.

Vehicle resolution:
  GET /api/state → vehicles dict → match by title → VehicleInfo(name, capacity, min_soc)

Plan endpoint:
  POST /api/vehicles/{name}/plan/soc/{soc}/{timestamp}
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import quote

import requests
import urllib3

from departic.models import EvccLiveStatus, VehicleInfo

if TYPE_CHECKING:
    from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)


class EvccError(Exception):
    pass


class EvccAPI:
    def __init__(self, url: str) -> None:
        self.base = url.rstrip("/") + "/api"
        self._session = requests.Session()
        self._session.verify = False  # allow self-signed certificates

    def _get(self, path: str) -> dict:
        r = self._session.get(f"{self.base}{path}", timeout=5)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str) -> dict:
        r = self._session.post(f"{self.base}{path}", timeout=5)
        r.raise_for_status()
        return r.json()

    # ── State ─────────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        """Return the full EVCC state."""
        return self._get("/state")

    def get_vehicle(self, vehicle_title: str) -> VehicleInfo | None:
        """
        Resolve vehicle info from EVCC state by matching vehicle_title.
        Returns None if no vehicle with the given title is found.
        """
        state = self.get_state()
        vehicles = state.get("vehicles", {})

        for name, info in vehicles.items():
            if info.get("title", "").lower() == vehicle_title.lower():
                min_soc = info.get("minSoc")
                return VehicleInfo(
                    name=name,
                    title=info.get("title", name),
                    capacity_kwh=info.get("capacity") or None,
                    min_soc_pct=int(min_soc) if min_soc else None,
                )

        log.warning("Vehicle with title %r not found in EVCC state.", vehicle_title)
        return None

    def get_avg_price(self) -> float | None:
        """
        Return the 30-day average energy price (€/kWh) from EVCC statistics.

        Reads /api/state → result.statistics["30d"].avgPrice.
        Returns None if the value is unavailable.
        """
        try:
            state = self.get_state()
            stats = state.get("statistics", {})
            avg_price = stats.get("30d", {}).get("avgPrice")
            if avg_price is not None:
                return float(avg_price)
        except (KeyError, TypeError, ValueError):
            log.warning("Could not read avgPrice from EVCC statistics.")
        return None

    def get_loadpoint_status(self, vehicle_title: str) -> EvccLiveStatus | None:
        """
        Return a live charging status snapshot for the loadpoint that has the
        configured vehicle connected (matched by vehicleTitle).

        Falls back to the first loadpoint if none matches (e.g. vehicle not
        connected anywhere). Returns None if EVCC is unreachable.
        """
        try:
            state = self.get_state()
        except Exception:  # noqa: BLE001
            log.warning("Could not fetch EVCC state for loadpoint status.")
            return None

        loadpoints: list[dict] = state.get("loadpoints", [])
        if not loadpoints:
            return None

        # Prefer the loadpoint where our vehicle is connected
        lp: dict | None = None
        for candidate in loadpoints:
            if candidate.get("vehicleTitle", "").lower() == vehicle_title.lower():
                lp = candidate
                break
        if lp is None:
            lp = loadpoints[0]

        # Current SoC — vehicleSoc is 0 when the car doesn't expose it;
        # treat 0 as unavailable
        raw_soc = lp.get("vehicleSoc")
        soc = int(raw_soc) if raw_soc else None

        # Session / charged energy (Wh → kWh); always store even when 0
        charged_wh = lp.get("chargedEnergy") or 0
        session_kwh = round(charged_wh / 1000, 2)

        # Charging power (W → kW); always store even when 0
        power_w = lp.get("chargePower") or 0
        power_kw = round(power_w / 1000, 2)

        mode = lp.get("mode")

        # Plan is stored on the vehicle in state.vehicles[name].plan, not the loadpoint.
        # Use the vehicleName from the loadpoint to look it up.
        vehicle_name = lp.get("vehicleName", "")
        vehicle_data = state.get("vehicles", {}).get(vehicle_name, {})
        v_plan = vehicle_data.get("plan") or {}
        plan_soc = v_plan.get("soc") or None
        plan_time = v_plan.get("time") or None
        if plan_time in ("0001-01-01T00:00:00Z", ""):
            plan_time = None
        if plan_soc == 0:
            plan_soc = None

        connected = bool(lp.get("connected"))

        # Energy still needed to reach target SoC (Wh → kWh)
        remaining_wh = lp.get("chargeRemainingEnergy") or 0
        charge_remaining_kwh = round(remaining_wh / 1000, 1) if remaining_wh else None

        # Site-level: solar generation (sum of all PV sources, W → kW)
        pv_sources = state.get("pv") or []
        pv_total_w = (
            sum(s.get("power", 0) for s in pv_sources)
            if isinstance(pv_sources, list)
            else 0
        )
        pv_power_kw = round(pv_total_w / 1000, 2) if pv_total_w else None

        # Site-level: grid power (W → kW; positive = import, negative = export)
        grid_w = (state.get("grid") or {}).get("power")
        grid_power_kw = round(grid_w / 1000, 2) if grid_w is not None else None

        # Current grid tariff
        tariff_raw = state.get("tariffGrid")
        tariff_grid_eur = round(float(tariff_raw), 4) if tariff_raw else None

        # 30-day solar share
        solar_pct_raw = (
            state.get("statistics", {}).get("30d", {}).get("solarPercentage")
        )
        solar_pct_30d = (
            round(float(solar_pct_raw), 1) if solar_pct_raw is not None else None
        )

        return EvccLiveStatus(
            vehicle_soc_pct=soc,
            session_energy_kwh=session_kwh,
            charge_power_kw=power_kw,
            charging_mode=mode,
            plan_soc_pct=plan_soc,
            plan_time=plan_time,
            vehicle_connected=connected,
            charge_remaining_kwh=charge_remaining_kwh,
            pv_power_kw=pv_power_kw,
            grid_power_kw=grid_power_kw,
            tariff_grid_eur=tariff_grid_eur,
            solar_pct_30d=solar_pct_30d,
        )

    # ── Interval ───────────────────────────────────────────────────────────

    def get_interval(self) -> int:
        """Return the EVCC polling interval in seconds (default 30)."""
        try:
            state = self.get_state()
            return int(state.get("interval", 30))
        except Exception:  # noqa: BLE001
            return 30

    # ── Plan ──────────────────────────────────────────────────────────────

    def has_plan_soc(self, vehicle_name: str) -> bool:
        """
        Check whether EVCC has an active SoC-based plan for the vehicle.
        Returns True (safe default) if the check fails.
        """
        try:
            r = self._session.get(
                f"{self.base}/vehicles/{quote(vehicle_name, safe=':')}/plan/soc",
                timeout=5,
            )
            if r.status_code == 404:
                return False
            r.raise_for_status()
            result = r.json().get("result", {})
            return bool(result.get("soc") and result.get("time"))
        except (requests.RequestException, KeyError):
            log.warning("Could not check plan status for %r", vehicle_name)
            return True  # assume plan exists to avoid hammering EVCC

    def delete_plan_soc(self, vehicle_name: str) -> None:
        """DELETE /api/vehicles/{name}/plan/soc"""
        r = self._session.delete(
            f"{self.base}/vehicles/{quote(vehicle_name, safe=':')}/plan/soc", timeout=5
        )
        r.raise_for_status()
        log.info("Vehicle plan deleted: %s", vehicle_name)

    def set_plan_soc(
        self, vehicle_name: str, target_soc_pct: int, charge_by: datetime
    ) -> None:
        """
        Set a SoC-based charging plan.
        POST /api/vehicles/{name}/plan/soc/{soc}/{timestamp}

        The timestamp is the time by which charging should be complete.
        Does not require the vehicle to be currently connected.
        EVCC will activate the plan when the vehicle plugs in.
        """
        ts = quote(charge_by.isoformat(), safe="-:.T")
        self._post(
            f"/vehicles/{quote(vehicle_name, safe=':')}/plan/soc/{target_soc_pct}/{ts}"
        )
        log.info(
            "Vehicle plan set: %s → %d%% SoC by %s",
            vehicle_name,
            target_soc_pct,
            charge_by.strftime("%d-%m %H:%M"),
        )
