"""Unit tests for ConfigCacheManager, ConfigPathResolver, and ProviderConfigManager.

Each class is a pure-logic utility that operates on in-memory state or delegates
to platform_dirs / schema constructors — no real filesystem, network, or AWS.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# ConfigCacheManager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigCacheManager:
    """ConfigCacheManager stores, retrieves, and expires typed config objects."""

    def _make_manager(self):
        from orb.config.managers.cache_manager import ConfigCacheManager

        return ConfigCacheManager()

    def test_get_cached_config_returns_none_when_empty(self):
        mgr = self._make_manager()
        assert mgr.get_cached_config(str) is None

    def test_cache_and_retrieve_config(self):
        mgr = self._make_manager()
        instance = MagicMock()
        mgr.cache_config(str, instance)
        assert mgr.get_cached_config(str) is instance

    def test_different_types_are_stored_independently(self):
        mgr = self._make_manager()
        obj_a = MagicMock(name="a")
        obj_b = MagicMock(name="b")
        mgr.cache_config(str, obj_a)
        mgr.cache_config(int, obj_b)
        assert mgr.get_cached_config(str) is obj_a
        assert mgr.get_cached_config(int) is obj_b

    def test_clear_cache_removes_all_entries(self):
        mgr = self._make_manager()
        mgr.cache_config(str, MagicMock())
        mgr.cache_config(int, MagicMock())
        mgr.clear_cache()
        assert mgr.get_cached_config(str) is None
        assert mgr.get_cached_config(int) is None

    def test_clear_config_cache_removes_only_target_type(self):
        mgr = self._make_manager()
        obj_a = MagicMock(name="a")
        obj_b = MagicMock(name="b")
        mgr.cache_config(str, obj_a)
        mgr.cache_config(int, obj_b)
        mgr.clear_config_cache(str)
        assert mgr.get_cached_config(str) is None
        assert mgr.get_cached_config(int) is obj_b

    def test_clear_config_cache_is_noop_for_missing_type(self):
        mgr = self._make_manager()
        # Should not raise
        mgr.clear_config_cache(str)

    def test_get_cache_stats_reflects_state(self):
        mgr = self._make_manager()
        mgr.cache_config(str, MagicMock())
        stats = mgr.get_cache_stats()
        assert stats["cache_size"] == 1
        assert "str" in stats["cached_types"]
        assert stats["last_reload_time"] is None

    def test_get_cache_stats_after_mark_reload(self):
        mgr = self._make_manager()
        t = time.time()
        mgr.mark_reload(t)
        stats = mgr.get_cache_stats()
        assert stats["last_reload_time"] == t

    def test_mark_reload_updates_last_reload_time(self):
        mgr = self._make_manager()
        t = 1_000_000.0
        mgr.mark_reload(t)
        assert mgr._last_reload_time == t

    def test_is_cache_valid_with_no_max_age_returns_true(self):
        mgr = self._make_manager()
        assert mgr.is_cache_valid() is True

    def test_is_cache_valid_with_max_age_and_no_reload_returns_false(self):
        mgr = self._make_manager()
        assert mgr.is_cache_valid(max_age_seconds=60) is False

    def test_is_cache_valid_with_fresh_reload_returns_true(self):
        mgr = self._make_manager()
        mgr.mark_reload(time.time())
        assert mgr.is_cache_valid(max_age_seconds=60) is True

    def test_is_cache_valid_with_expired_reload_returns_false(self):
        mgr = self._make_manager()
        # Mark a reload 200 seconds ago
        mgr.mark_reload(time.time() - 200)
        assert mgr.is_cache_valid(max_age_seconds=60) is False


# ---------------------------------------------------------------------------
# ConfigPathResolver
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigPathResolver:
    """ConfigPathResolver resolves directory and file paths without touching the FS."""

    def _make_resolver(self, base_config_path=None):
        from orb.config.managers.path_resolver import ConfigPathResolver

        return ConfigPathResolver(base_config_path=base_config_path)

    def test_resolve_path_with_explicit_config_path(self, tmp_path):
        resolver = self._make_resolver()
        result = resolver.resolve_path("work", "default/work", str(tmp_path))
        assert result == str(tmp_path)

    def test_resolve_path_creates_directory(self, tmp_path):
        resolver = self._make_resolver()
        new_dir = tmp_path / "subdir"
        resolver.resolve_path("work", "default/work", str(new_dir))
        assert new_dir.exists()

    def test_resolve_path_platform_routing_for_work(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORB_ROOT_DIR", str(tmp_path))
        resolver = self._make_resolver()
        result = resolver.resolve_path("work", "fallback")
        # Should route through get_work_location → ORB_ROOT_DIR / work
        assert result == str(tmp_path / "work")

    def test_resolve_path_platform_routing_for_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORB_ROOT_DIR", str(tmp_path))
        resolver = self._make_resolver()
        result = resolver.resolve_path("config", "fallback")
        assert result == str(tmp_path / "config")

    def test_resolve_path_platform_routing_for_logs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORB_ROOT_DIR", str(tmp_path))
        resolver = self._make_resolver()
        result = resolver.resolve_path("log", "fallback")
        assert result == str(tmp_path / "logs")

    def test_resolve_path_with_base_config_path(self, tmp_path):
        # When path_type is unknown and base_config_path is set, resolve relative to it
        cfg_file = tmp_path / "config" / "settings.yaml"
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.touch()
        resolver = self._make_resolver(base_config_path=str(cfg_file))
        result = resolver.resolve_path("unknown_type", "data")
        # Should be <tmp_path>/data (base_dir is parent of parent of cfg_file = tmp_path)
        assert Path(result).is_absolute()

    def test_resolve_path_fallback_when_no_base_and_unknown_type(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        resolver = self._make_resolver()
        result = resolver.resolve_path("unknown_type", "data")
        assert Path(result).is_absolute()
        assert result.endswith("data")

    def test_resolve_path_raises_on_permission_error(self, tmp_path):
        resolver = self._make_resolver()
        with patch("os.makedirs", side_effect=PermissionError("denied")):
            from orb.domain.base.exceptions import ConfigurationError

            with pytest.raises(ConfigurationError, match="permission denied"):
                resolver.resolve_path("unknown_type", "data", str(tmp_path / "noperm"))

    def test_resolve_file_with_absolute_config_path(self, tmp_path):
        resolver = self._make_resolver()
        abs_path = str(tmp_path / "myfile.yaml")
        result = resolver.resolve_file("config", "ignored.yaml", config_path=abs_path)
        assert result == abs_path

    def test_resolve_file_with_relative_config_path_and_base(self, tmp_path):
        cfg_file = tmp_path / "settings.yaml"
        cfg_file.touch()
        resolver = self._make_resolver(base_config_path=str(cfg_file))
        result = resolver.resolve_file("config", "ignored.yaml", config_path="extra.yaml")
        assert result == str(tmp_path / "extra.yaml")

    def test_resolve_file_with_relative_config_path_no_base(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        resolver = self._make_resolver()
        result = resolver.resolve_file("config", "ignored.yaml", config_path="extra.yaml")
        assert Path(result).is_absolute()
        assert result.endswith("extra.yaml")

    def test_resolve_file_with_default_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORB_ROOT_DIR", str(tmp_path))
        resolver = self._make_resolver()
        result = resolver.resolve_file("config", "app.yaml", default_dir="config")
        assert result.endswith("app.yaml")

    def test_resolve_file_no_dir_no_config_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        resolver = self._make_resolver()
        result = resolver.resolve_file("config", "app.yaml")
        assert Path(result).is_absolute()
        assert result.endswith("app.yaml")

    def test_get_work_dir_uses_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORB_ROOT_DIR", str(tmp_path))
        resolver = self._make_resolver()
        result = resolver.get_work_dir()
        assert result == str(tmp_path / "work")

    def test_get_config_dir_uses_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORB_ROOT_DIR", str(tmp_path))
        resolver = self._make_resolver()
        result = resolver.get_config_dir()
        assert result == str(tmp_path / "config")

    def test_get_log_dir_uses_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORB_ROOT_DIR", str(tmp_path))
        resolver = self._make_resolver()
        result = resolver.get_log_dir()
        assert result == str(tmp_path / "logs")

    def test_get_events_dir_with_explicit_path(self, tmp_path):
        resolver = self._make_resolver()
        result = resolver.get_events_dir(config_path=str(tmp_path))
        assert result == str(tmp_path)

    def test_get_cache_dir_uses_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORB_ROOT_DIR", str(tmp_path))
        resolver = self._make_resolver()
        result = resolver.get_cache_dir()
        assert result == str(tmp_path / "work" / ".cache")

    def test_get_snapshots_dir_with_explicit_path(self, tmp_path):
        resolver = self._make_resolver()
        result = resolver.get_snapshots_dir(config_path=str(tmp_path))
        assert result == str(tmp_path)


# ---------------------------------------------------------------------------
# ProviderConfigManager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderConfigManager:
    """ProviderConfigManager reads and writes provider config from a raw dict."""

    def _make_manager(self, raw_config=None):
        from orb.config.managers.provider_manager import ProviderConfigManager

        return ProviderConfigManager(raw_config=raw_config or {})

    def test_get_storage_strategy_default(self):
        mgr = self._make_manager()
        assert mgr.get_storage_strategy() == "json"

    def test_get_storage_strategy_from_config(self):
        mgr = self._make_manager({"storage": {"strategy": "sql"}})
        assert mgr.get_storage_strategy() == "sql"

    def test_get_scheduler_strategy_default(self):
        mgr = self._make_manager()
        assert mgr.get_scheduler_strategy() == "default"

    def test_get_scheduler_strategy_from_config(self):
        mgr = self._make_manager({"scheduler": {"type": "hf"}})
        assert mgr.get_scheduler_strategy() == "hf"

    def test_get_provider_config_returns_none_when_empty(self):
        mgr = self._make_manager()
        assert mgr.get_provider_config() is None

    def test_get_provider_config_parses_provider_section(self):
        raw = {
            "provider": {
                "selection_policy": "FIRST_AVAILABLE",
                "providers": [],
            }
        }
        mgr = self._make_manager(raw)
        pc = mgr.get_provider_config()
        assert pc is not None
        assert pc.selection_policy == "FIRST_AVAILABLE"

    def test_is_provider_strategy_enabled_false_when_single(self):
        raw = {"provider": {"mode": "single"}}
        mgr = self._make_manager(raw)
        # single mode → not multi → False
        assert mgr.is_provider_strategy_enabled() is False

    def test_is_provider_strategy_enabled_true_when_multi(self):
        raw = {"provider": {"mode": "multi"}}
        mgr = self._make_manager(raw)
        assert mgr.is_provider_strategy_enabled() is True

    def test_get_provider_mode_default(self):
        mgr = self._make_manager()
        from orb.config.schemas.provider_strategy_schema import ProviderMode

        assert mgr.get_provider_mode() == ProviderMode.SINGLE.value

    def test_get_provider_mode_from_config(self):
        raw = {"provider": {"mode": "multi"}}
        mgr = self._make_manager(raw)
        assert mgr.get_provider_mode() == "multi"

    def test_is_multi_provider_mode_false_with_no_providers(self):
        raw = {"provider": {"selection_policy": "FIRST_AVAILABLE", "providers": []}}
        mgr = self._make_manager(raw)
        assert mgr.is_multi_provider_mode() is False

    def test_is_multi_provider_mode_false_with_one_provider(self):
        raw = {
            "provider": {
                "selection_policy": "FIRST_AVAILABLE",
                "providers": [{"name": "p1", "type": "aws", "enabled": True}],
            }
        }
        mgr = self._make_manager(raw)
        assert mgr.is_multi_provider_mode() is False

    def test_is_multi_provider_mode_true_with_two_providers(self):
        raw = {
            "provider": {
                "selection_policy": "FIRST_AVAILABLE",
                "providers": [
                    {"name": "p1", "type": "aws", "enabled": True},
                    {"name": "p2", "type": "aws", "enabled": True},
                ],
            }
        }
        mgr = self._make_manager(raw)
        assert mgr.is_multi_provider_mode() is True

    def test_get_active_provider_names_empty_when_no_providers(self):
        mgr = self._make_manager()
        assert mgr.get_active_provider_names() == []

    def test_get_active_provider_names_returns_enabled_names(self):
        raw = {
            "provider": {
                "selection_policy": "FIRST_AVAILABLE",
                "providers": [
                    {"name": "enabled-one", "type": "aws", "enabled": True},
                    {"name": "disabled-one", "type": "aws", "enabled": False},
                ],
            }
        }
        mgr = self._make_manager(raw)
        names = mgr.get_active_provider_names()
        assert "enabled-one" in names
        assert "disabled-one" not in names

    def test_get_provider_instance_config_returns_none_for_unknown_name(self):
        raw = {
            "provider": {
                "selection_policy": "FIRST_AVAILABLE",
                "providers": [{"name": "p1", "type": "aws"}],
            }
        }
        mgr = self._make_manager(raw)
        assert mgr.get_provider_instance_config("nonexistent") is None

    def test_get_provider_instance_config_returns_correct_instance(self):
        raw = {
            "provider": {
                "selection_policy": "FIRST_AVAILABLE",
                "providers": [
                    {"name": "p1", "type": "aws"},
                    {"name": "p2", "type": "k8s"},
                ],
            }
        }
        mgr = self._make_manager(raw)
        inst = mgr.get_provider_instance_config("p2")
        assert inst is not None
        assert inst.name == "p2"
        assert inst.type == "k8s"

    def test_save_provider_config_writes_to_raw_config(self):
        raw = {"provider": {"selection_policy": "FIRST_AVAILABLE", "providers": []}}
        mgr2 = self._make_manager(raw)
        from orb.config.schemas.provider_strategy_schema import ProviderConfig

        pc = ProviderConfig(selection_policy="FIRST_AVAILABLE", providers=[])  # type: ignore[call-arg]
        mgr2.save_provider_config(pc)
        assert "provider" in mgr2._raw_config

    def test_save_provider_config_raises_on_error(self):
        from orb.domain.base.exceptions import ConfigurationError

        mgr = self._make_manager()
        # Pass a non-pydantic, non-dict-able object that will fail
        broken = MagicMock(spec=[])  # no model_dump, no __dict__
        broken.model_dump = MagicMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ConfigurationError, match="Failed to save provider configuration"):
            mgr.save_provider_config(broken)  # type: ignore[arg-type]

    def test_nested_value_missing_key_returns_default(self):
        mgr = self._make_manager({"a": {"b": 42}})
        assert mgr._get_nested_value("a.c", "default") == "default"

    def test_nested_value_non_dict_intermediate_returns_default(self):
        mgr = self._make_manager({"a": "not-a-dict"})
        assert mgr._get_nested_value("a.b", "default") == "default"
