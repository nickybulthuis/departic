from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import yaml

from departic.config import DATA_DIR
from departic.models import Coords, RouteResult

if TYPE_CHECKING:
    from pathlib import Path

    from departic.config import Settings

log = logging.getLogger(__name__)


class RouteCache:
    """
    Route cache: (origin, destination) → RouteResult(km, duration_s).

    Persisted to disk so results survive restarts.
    """

    def __init__(self, cache_file: Path | None = None) -> None:
        self._file = cache_file or DATA_DIR / "routecache.json"
        self._data: dict[tuple[str, str], RouteResult] = {}

    def get(self, origin: str, destination: str) -> RouteResult | None:
        return self._data.get((origin, destination))

    def set(
        self, origin: str, destination: str, km: float, duration_s: float | None = None
    ) -> None:

        self._data[(origin, destination)] = RouteResult(
            distance_km=km, duration_s=duration_s
        )

    def load(self) -> None:
        """Load persisted entries from disk."""

        if self._file.exists():
            try:
                raw = json.loads(self._file.read_text())
                self._data = {  # type: ignore[misc]
                    tuple(k.split("|", 1)): RouteResult(
                        distance_km=v[0], duration_s=v[1] if len(v) > 1 else None
                    )
                    for k, v in raw.items()
                }
                log.info("Route cache loaded: %d routes.", len(self._data))
            except OSError:
                log.warning("Could not load route cache — starting with empty cache.")

    def save(self) -> None:
        """Persist current entries to disk."""
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            serializable = {
                f"{k[0]}|{k[1]}": [v.distance_km, v.duration_s]
                for k, v in self._data.items()
            }
            self._file.write_text(json.dumps(serializable, indent=2))
        except OSError:
            log.warning("Could not save route cache.")

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


class GeocodeCache:
    """
    Geocode cache: address → Coords(lat, lon).

    Places don't move, so persist to disk so results survive restarts.
    """

    def __init__(self, cache_file: Path | None = None) -> None:
        self._file = cache_file or DATA_DIR / "geocache.json"
        self._data: dict[str, Coords] = {}

    def get(self, address: str) -> Coords | None:
        return self._data.get(address)

    def set(self, address: str, coords: Coords) -> None:
        self._data[address] = coords

    def load(self) -> None:
        """Load persisted entries from disk."""
        if self._file.exists():
            try:
                data = json.loads(self._file.read_text())
                self._data = {k: Coords(*v) for k, v in data.items()}
                log.info("Geocache loaded: %d addresses.", len(self._data))
            except OSError:
                log.warning("Could not load geocache — starting with empty cache.")

    def save(self) -> None:
        """Persist current entries to disk."""
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(
                json.dumps({k: list(v) for k, v in self._data.items()}, indent=2)
            )
        except OSError:
            log.warning("Could not save geocache.")

    def clear(self) -> None:
        self._data.clear()

    def __contains__(self, address: str) -> bool:
        return address in self._data

    def __len__(self) -> int:
        return len(self._data)


class SettingsCache:
    """
    Cache for the parsed Settings object.
    """

    def __init__(self, config_file: Path | None = None) -> None:
        self._file = config_file or DATA_DIR / "departic.yaml"
        self._settings: Settings | None = None
        self._mtime: float = 0.0

    def _load(self) -> Settings:
        """Parse and validate the YAML configuration file."""
        from departic.config import Settings  # noqa: PLC0415 — circular import guard

        if not self._file.exists():
            msg = (
                f"Configuration file not found: {self._file}\n"
                f"Copy departic.example.yaml to {self._file} and edit it."
            )
            raise FileNotFoundError(msg)
        with self._file.open() as f:
            data = yaml.safe_load(f)
        return Settings(**data)

    def get(self) -> Settings | None:
        """Return cached settings, reloading from disk if the file has changed."""
        try:
            mtime = self._file.stat().st_mtime
        except OSError:
            if self._settings is None:
                log.warning("Configuration file not found: %s", self._file)
            return self._settings

        if mtime != self._mtime:
            try:
                self._settings = self._load()
                self._mtime = mtime
                log.info("Configuration loaded from %s", self._file)
            except Exception:
                log.exception("Error in configuration file")
                return None

        return self._settings

    def invalidate(self) -> None:
        """Force a reload on the next call to get()."""
        self._mtime = 0.0

    def clear(self) -> None:
        self._settings = None
        self._mtime = 0.0


# Module-level singletons — shared across the application
route_cache = RouteCache()
geocode_cache = GeocodeCache()
settings_cache = SettingsCache()
