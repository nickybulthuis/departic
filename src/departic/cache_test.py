"""
Tests for the cache module.
"""

from unittest.mock import patch

from departic.cache import GeocodeCache, RouteCache, SettingsCache
from departic.config import Settings

_VALID_YAML = (
    "evcc:\n  url: http://evcc.test\nagenda:\n  feeds: []\n  trip_mapping: []\n"
)
_VALID_YAML_WITH_FEED = (
    "evcc:\n  url: http://evcc.test\n"
    "agenda:\n  feeds:\n    - url: http://cal.test\n  trip_mapping: []\n"
)


def test_settings_cache_returns_none_for_missing_file(tmp_path):
    cache = SettingsCache(config_file=tmp_path / "nonexistent.yaml")
    result = cache.get()
    assert result is None


def test_settings_cache_loads_valid_file(tmp_path):
    f = tmp_path / "departic.yaml"
    f.write_text(_VALID_YAML)
    cache = SettingsCache(config_file=f)
    result = cache.get()
    assert isinstance(result, Settings)


def test_settings_cache_reuses_on_same_mtime(tmp_path):
    f = tmp_path / "departic.yaml"
    f.write_text(_VALID_YAML)
    cache = SettingsCache(config_file=f)
    result1 = cache.get()
    result2 = cache.get()
    assert result1 is result2


def test_settings_cache_invalidate(tmp_path):
    f = tmp_path / "departic.yaml"
    f.write_text(_VALID_YAML)
    cache = SettingsCache(config_file=f)
    result1 = cache.get()
    cache.invalidate()
    result2 = cache.get()
    assert result1 is not result2


def test_route_cache_miss():
    cache = RouteCache()
    assert cache.get("A", "B") is None


def test_route_cache_set_and_get():
    cache = RouteCache()
    cache.set("Smallville", "Gotham", 35.0, 1800.0)
    assert cache.get("Smallville", "Gotham") == (35.0, 1800.0)
    assert cache.get("Gotham", "Smallville") is None


def test_route_cache_clear():
    cache = RouteCache()
    cache.set("A", "B", 10.0, 600.0)
    cache.clear()
    assert cache.get("A", "B") is None
    assert len(cache) == 0


def test_route_cache_load_save(tmp_path):
    f = tmp_path / "routecache.json"
    cache = RouteCache(cache_file=f)
    cache.set("Smallville", "Gotham", 35.0, 1800.0)
    cache.save()
    assert f.exists()

    cache2 = RouteCache(cache_file=f)
    cache2.load()
    assert cache2.get("Smallville", "Gotham") == (35.0, 1800.0)


def test_route_cache_load_os_error(tmp_path):
    """RouteCache.load() swallows OSError and starts empty."""
    cache = RouteCache(cache_file=tmp_path / "routecache.json")
    (tmp_path / "routecache.json").write_text("{}")
    with patch.object(
        cache._file.__class__, "read_text", side_effect=OSError("disk error")
    ):
        cache.load()
    assert len(cache) == 0


def test_route_cache_save_os_error(tmp_path):
    """RouteCache.save() swallows OSError."""
    cache = RouteCache(cache_file=tmp_path / "routecache.json")
    cache.set("A", "B", 10.0)
    with patch.object(
        cache._file.__class__, "write_text", side_effect=OSError("disk full")
    ):
        cache.save()  # should not raise


def test_geocode_cache_miss():
    cache = GeocodeCache()
    assert cache.get("Metropolis") is None


def test_geocode_cache_set_and_get():
    cache = GeocodeCache()
    cache.set("Metropolis", (10.123, 20.456))
    assert cache.get("Metropolis") == (10.123, 20.456)


def test_geocode_cache_contains():
    cache = GeocodeCache()
    cache.set("Metropolis", (10.123, 20.456))
    assert "Metropolis" in cache
    assert "Gotham" not in cache


def test_geocode_cache_clear():
    cache = GeocodeCache()
    cache.set("Metropolis", (10.123, 20.456))
    cache.clear()
    assert cache.get("Metropolis") is None
    assert len(cache) == 0


def test_geocode_cache_load_save(tmp_path):
    f = tmp_path / "geocache.json"
    cache = GeocodeCache(cache_file=f)
    cache.set("Smallville", (30.789, 40.321))
    cache.save()
    assert f.exists()

    cache2 = GeocodeCache(cache_file=f)
    cache2.load()
    assert cache2.get("Smallville") == (30.789, 40.321)


def test_geocode_cache_load_os_error(tmp_path):
    """GeocodeCache.load() swallows OSError and starts empty."""
    cache = GeocodeCache(cache_file=tmp_path / "geocache.json")
    # Create the file so .exists() returns True, then make read_text fail
    (tmp_path / "geocache.json").write_text("{}")
    with patch.object(
        cache._file.__class__, "read_text", side_effect=OSError("disk error")
    ):
        cache.load()
    assert len(cache) == 0


def test_geocode_cache_save_os_error(tmp_path):
    """GeocodeCache.save() swallows OSError."""
    cache = GeocodeCache(cache_file=tmp_path / "geocache.json")
    cache.set("Metropolis", (10.0, 20.0))
    with patch.object(
        cache._file.__class__, "write_text", side_effect=OSError("disk full")
    ):
        cache.save()  # should not raise


def test_settings_cache_clear(tmp_path):
    f = tmp_path / "departic.yaml"
    f.write_text(_VALID_YAML)
    cache = SettingsCache(config_file=f)
    cache.get()
    cache.clear()
    assert cache._settings is None
    assert cache._mtime == 0.0


def test_settings_cache_returns_none_on_bad_yaml(tmp_path):
    """SettingsCache.get() returns None (not raises) when YAML is invalid."""
    f = tmp_path / "departic.yaml"
    f.write_text("evcc: {bad yaml: [}")
    cache = SettingsCache(config_file=f)
    result = cache.get()
    assert result is None


def test_settings_get_and_reload(tmp_path):
    """Settings.get() and Settings.reload() delegate to the module cache."""
    f = tmp_path / "departic.yaml"
    f.write_text(_VALID_YAML_WITH_FEED)

    cache = SettingsCache(config_file=f)
    with patch("departic.cache.settings_cache", cache):
        result = Settings.get()
        assert isinstance(result, Settings)
        assert result.is_configured() is True

        result2 = Settings.reload()
        assert isinstance(result2, Settings)


def test_agenda_config_is_configured(tmp_path):
    """AgendaConfig.is_configured() returns True only when feeds is non-empty."""
    f = tmp_path / "departic.yaml"
    f.write_text(_VALID_YAML)
    cache = SettingsCache(config_file=f)
    result = cache.get()
    assert result is not None
    assert result.agenda.is_configured() is False  # no feeds in _VALID_YAML

    f.write_text(_VALID_YAML_WITH_FEED)
    cache.invalidate()
    result2 = cache.get()
    assert result2 is not None
    assert result2.agenda.is_configured() is True
