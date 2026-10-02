"""Unit tests for ConfigurationManager covering previously uncovered branches.

Targets:
  - config_file property (line 89)
  - loader property lazy init (lines 94-97)
  - app_config property / _load_app_config error path (lines 103-114)
  - _ensure_raw_config both branches (lines 118-126)
  - get_typed / get_typed_with_defaults fallback (lines 149-174)
  - reload() full path and error path (lines 176-213)
  - get / get_bool / get_int / get_float / get_str / set / update delegates (lines 216-246)
  - resolve_path / get_work_dir / get_cache_dir / get_config_dir / get_log_dir (lines 249-277)
  - get_storage_strategy / scheduler override / provider overrides (lines 280-312)
  - get_loaded_config_file: existing and missing file (lines 314-329)
  - save() success and error (lines 343-352)
  - get_raw_config (line 354-356)
  - resolve_file: explicit path with dir, explicit bare filename, scheduler path, default fallback (lines 358-420)
  - find_templates_file: default and provider-specific, FileNotFoundError (lines 454-500)
  - get_cache_stats (lines 502-504)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manager_from_dict(cfg: dict | None = None):
    """Return a ConfigurationManager built from an in-memory dict (no file I/O)."""
    from orb.config.managers.configuration_manager import ConfigurationManager

    with patch("orb.config.loader.ConfigurationLoader._build_raw_config_from_dict") as mock_build:
        mock_build.return_value = cfg or {}
        mgr = ConfigurationManager(config_dict=cfg or {})
        # Force raw-config to be the plain dict so methods work synchronously.
        mgr._raw_config = cfg or {}
    return mgr


# ---------------------------------------------------------------------------
# config_file property
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigFileProperty:
    def test_returns_none_when_built_from_dict_without_file(self):
        from orb.config.managers.configuration_manager import ConfigurationManager

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {}
        assert mgr.config_file is None

    def test_returns_path_when_built_with_explicit_path(self, tmp_path):
        from orb.config.managers.configuration_manager import ConfigurationManager

        p = str(tmp_path / "cfg.json")
        mgr = ConfigurationManager(config_file=p, config_dict={})
        mgr._raw_config = {}
        assert mgr.config_file == p


# ---------------------------------------------------------------------------
# loader property
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoaderProperty:
    def test_lazy_creates_loader_on_first_access(self):
        from orb.config.managers.configuration_manager import ConfigurationManager

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {}
        assert mgr._loader is None
        loader = mgr.loader
        assert loader is not None
        # second access returns same instance
        assert mgr.loader is loader


# ---------------------------------------------------------------------------
# app_config / _load_app_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAppConfig:
    def test_app_config_cached_after_first_access(self):
        from orb.config.managers.configuration_manager import ConfigurationManager

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {}

        fake_app_config = MagicMock()
        mock_loader = MagicMock()
        mock_loader.create_app_config.return_value = fake_app_config
        mgr._loader = mock_loader

        first = mgr.app_config
        second = mgr.app_config
        assert first is second
        mock_loader.create_app_config.assert_called_once()

    def test_load_app_config_re_raises_exception(self):
        from orb.config.managers.configuration_manager import ConfigurationManager

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {}

        mock_loader = MagicMock()
        mock_loader.create_app_config.side_effect = ValueError("bad config")
        mgr._loader = mock_loader

        with pytest.raises(ValueError, match="bad config"):
            _ = mgr.app_config


# ---------------------------------------------------------------------------
# get_typed / get_typed_with_defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTyped:
    def _make_mgr(self, raw: dict):
        from orb.config.managers.configuration_manager import ConfigurationManager
        from orb.config.managers.type_converter import ConfigTypeConverter

        mgr = ConfigurationManager(config_dict=raw)
        mgr._raw_config = raw
        mgr._type_converter = ConfigTypeConverter(raw)
        return mgr

    def test_get_typed_uses_cache_on_second_call(self):
        class FakeCfg:
            def __init__(self, **kw):
                pass

        mgr = self._make_mgr({"fakecfg": {}})
        first = mgr.get_typed(FakeCfg)
        second = mgr.get_typed(FakeCfg)
        # same object returned from cache
        assert first is second

    def test_get_typed_with_defaults_returns_default_on_error(self):
        class SomeCfg:
            def __init__(self):
                self.built_from_defaults = True

        from orb.config.managers.configuration_manager import ConfigurationManager

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {}

        # When get_typed raises, the documented contract is to swallow the error
        # and fall back to config_type() defaults.
        with patch.object(mgr, "get_typed", side_effect=RuntimeError("bad")):
            result = mgr.get_typed_with_defaults(SomeCfg)

        # The original RuntimeError must NOT propagate; a default instance is returned.
        assert isinstance(result, SomeCfg)
        assert result.built_from_defaults is True


# ---------------------------------------------------------------------------
# reload()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReload:
    def test_reload_clears_all_caches(self):
        from orb.config.managers.configuration_manager import ConfigurationManager

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {"key": "val"}
        mgr._app_config = MagicMock()
        mgr._type_converter = MagicMock()
        mgr._path_resolver = MagicMock()
        mgr._provider_manager = MagicMock()
        mgr._config_file = None  # keep _config_dict intact after reload

        mgr.reload()

        assert mgr._raw_config is None
        assert mgr._app_config is None
        assert mgr._type_converter is None
        assert mgr._path_resolver is None
        assert mgr._provider_manager is None

    def test_reload_clears_config_dict_when_config_file_set(self, tmp_path):
        from orb.config.managers.configuration_manager import ConfigurationManager

        p = tmp_path / "cfg.json"
        p.write_text("{}")
        mgr = ConfigurationManager(config_file=str(p), config_dict={"a": 1})
        mgr._raw_config = {"a": 1}
        mgr.reload()
        # _config_dict cleared so next access goes to disk
        assert mgr._config_dict is None

    def test_reload_calls_loader_reload_if_supported(self):
        from orb.config.managers.configuration_manager import ConfigurationManager

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {}
        mock_loader = MagicMock(spec=["reload"])
        mock_loader.reload = MagicMock()
        mgr._loader = mock_loader
        mgr._config_file = None

        mgr.reload()
        mock_loader.reload.assert_called_once()

    def test_reload_wraps_exception_in_configuration_error(self):
        from orb.config.managers.configuration_manager import ConfigurationManager
        from orb.domain.base.exceptions import ConfigurationError

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {}
        mgr._config_file = None

        # Patch cache manager to raise on clear_cache
        mgr._cache_manager = MagicMock()
        mgr._cache_manager.clear_cache.side_effect = RuntimeError("disk full")
        mgr._cache_manager.mark_reload = MagicMock()

        with pytest.raises(ConfigurationError, match="reload failed"):
            mgr.reload()


# ---------------------------------------------------------------------------
# Type-conversion delegate methods
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDelegateMethods:
    def _make_mgr(self, raw: dict):
        from orb.config.managers.configuration_manager import ConfigurationManager
        from orb.config.managers.type_converter import ConfigTypeConverter

        mgr = ConfigurationManager(config_dict=raw)
        mgr._raw_config = raw
        mgr._type_converter = ConfigTypeConverter(raw)
        return mgr

    def test_get_returns_value(self):
        mgr = self._make_mgr({"foo": "bar"})
        assert mgr.get("foo") == "bar"

    def test_get_returns_default_for_missing_key(self):
        mgr = self._make_mgr({})
        assert mgr.get("no_such_key", "default_val") == "default_val"

    def test_get_bool_true_string(self):
        mgr = self._make_mgr({"flag": "yes"})
        assert mgr.get_bool("flag") is True

    def test_get_bool_false_bool(self):
        mgr = self._make_mgr({"flag": False})
        assert mgr.get_bool("flag") is False

    def test_get_int_returns_integer(self):
        mgr = self._make_mgr({"count": "42"})
        assert mgr.get_int("count") == 42

    def test_get_int_returns_default_for_invalid(self):
        mgr = self._make_mgr({"count": "not-a-number"})
        assert mgr.get_int("count", default=7) == 7

    def test_get_float_returns_float(self):
        mgr = self._make_mgr({"ratio": "3.14"})
        assert mgr.get_float("ratio") == pytest.approx(3.14)

    def test_get_float_returns_default_for_invalid(self):
        mgr = self._make_mgr({"ratio": "bad"})
        assert mgr.get_float("ratio", default=0.5) == pytest.approx(0.5)

    def test_get_str_returns_string(self):
        mgr = self._make_mgr({"name": 42})
        assert mgr.get_str("name") == "42"

    def test_set_creates_key(self):
        mgr = self._make_mgr({})
        mgr.set("new_key", "hello")
        assert mgr.get("new_key") == "hello"

    def test_set_nested_dot_notation(self):
        mgr = self._make_mgr({})
        mgr.set("a.b.c", 99)
        assert mgr.get("a.b.c") == 99

    def test_set_clears_cache(self):
        mgr = self._make_mgr({})
        mgr._cache_manager = MagicMock()
        mgr.set("x", 1)
        mgr._cache_manager.clear_cache.assert_called()

    def test_update_merges_values(self):
        mgr = self._make_mgr({"a": 1, "b": 2})
        mgr.update({"b": 99, "c": 3})
        assert mgr.get("b") == 99
        assert mgr.get("c") == 3

    def test_update_clears_cache(self):
        mgr = self._make_mgr({})
        mgr._cache_manager = MagicMock()
        mgr.update({"x": 1})
        mgr._cache_manager.clear_cache.assert_called()


# ---------------------------------------------------------------------------
# Scheduler and provider override methods
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSchedulerProviderOverrides:
    def _make_mgr(self):
        from orb.config.managers.configuration_manager import ConfigurationManager

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {}
        return mgr

    def test_override_and_restore_scheduler_strategy(self):
        mgr = self._make_mgr()
        mgr.override_scheduler_strategy("hostfactory")
        assert mgr._scheduler_override == "hostfactory"
        mgr.restore_scheduler_strategy()
        assert mgr._scheduler_override is None

    def test_get_scheduler_strategy_uses_override(self):
        mgr = self._make_mgr()
        mgr._provider_manager = MagicMock()
        mgr._provider_manager.get_scheduler_strategy.return_value = "default"
        mgr.override_scheduler_strategy("myscheduler")
        assert mgr.get_scheduler_strategy() == "myscheduler"

    def test_get_scheduler_strategy_delegates_without_override(self):
        mgr = self._make_mgr()
        mgr._provider_manager = MagicMock()
        mgr._provider_manager.get_scheduler_strategy.return_value = "default"
        assert mgr.get_scheduler_strategy() == "default"


# ---------------------------------------------------------------------------
# get_loaded_config_file
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetLoadedConfigFile:
    def test_returns_path_when_file_exists(self, tmp_path):
        from orb.config.managers.configuration_manager import ConfigurationManager

        p = tmp_path / "config.json"
        p.write_text("{}")
        mgr = ConfigurationManager(config_file=str(p), config_dict={})
        mgr._raw_config = {}
        assert mgr.get_loaded_config_file() == str(p)

    def test_returns_none_when_file_does_not_exist(self, tmp_path):
        from orb.config.managers.configuration_manager import ConfigurationManager

        mgr = ConfigurationManager(config_file="/does/not/exist/config.json", config_dict={})
        mgr._raw_config = {}

        # Point the platform fallback at an empty directory containing no
        # config.json so the discovery path deterministically finds nothing.
        empty_dir = tmp_path / "empty_config_dir"
        empty_dir.mkdir()
        with patch("orb.config.platform_dirs.get_config_location", return_value=empty_dir):
            result = mgr.get_loaded_config_file()

        assert result is None


# ---------------------------------------------------------------------------
# get_source_config_file
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSourceConfigFile:
    def test_returns_construction_path_when_it_exists(self, tmp_path):
        from orb.config.managers.configuration_manager import ConfigurationManager

        p = tmp_path / "config.json"
        p.write_text("{}")
        mgr = ConfigurationManager(config_file=str(p), config_dict={})
        mgr._raw_config = {}
        assert mgr.get_source_config_file() == str(p)

    def test_returns_none_when_construction_path_absent(self, tmp_path):
        from orb.config.managers.configuration_manager import ConfigurationManager

        mgr = ConfigurationManager(config_file=str(tmp_path / "missing.json"), config_dict={})
        mgr._raw_config = {}
        assert mgr.get_source_config_file() is None

    def test_does_not_synthesise_from_env(self, tmp_path, monkeypatch):
        # An in-memory config with no backing file must not resolve a save
        # target from ORB_CONFIG_FILE even when that file exists on disk.
        from orb.config.managers.configuration_manager import ConfigurationManager

        env_file = tmp_path / "env_config.json"
        env_file.write_text("{}")
        monkeypatch.setenv("ORB_CONFIG_FILE", str(env_file))

        mgr = ConfigurationManager(config_dict={})
        mgr._config_file = None
        mgr._raw_config = {}
        assert mgr.get_source_config_file() is None


# ---------------------------------------------------------------------------
# save()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSave:
    def test_save_writes_json_to_file(self, tmp_path):
        from orb.config.managers.configuration_manager import ConfigurationManager

        raw = {"key": "value", "num": 1}
        mgr = ConfigurationManager(config_dict=raw)
        mgr._raw_config = raw

        out = tmp_path / "output.json"
        mgr.save(str(out))

        loaded = json.loads(out.read_text())
        assert loaded == raw

    def test_save_raises_configuration_error_on_failure(self):
        from orb.config.managers.configuration_manager import ConfigurationManager
        from orb.domain.base.exceptions import ConfigurationError

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {"x": 1}

        # A path under a location the process cannot create/write must surface
        # as a ConfigurationError rather than a raw OSError.
        with patch("os.replace", side_effect=OSError("boom")):
            with pytest.raises(ConfigurationError, match="Failed to save"):
                mgr.save("/nonexistent_directory_abc/output.json")

    def test_save_creates_parent_directories(self, tmp_path):
        from orb.config.managers.configuration_manager import ConfigurationManager

        raw = {"key": "value"}
        mgr = ConfigurationManager(config_dict=raw)
        mgr._raw_config = raw

        out = tmp_path / "nested" / "deeper" / "output.json"
        mgr.save(str(out))

        assert json.loads(out.read_text()) == raw

    def test_save_is_atomic_no_temp_files_left_on_success(self, tmp_path):
        from orb.config.managers.configuration_manager import ConfigurationManager

        raw = {"key": "value"}
        mgr = ConfigurationManager(config_dict=raw)
        mgr._raw_config = raw

        out = tmp_path / "output.json"
        mgr.save(str(out))

        # Only the target file should exist — no orphaned .tmp scratch files.
        remaining = list(tmp_path.iterdir())
        assert remaining == [out]

    def test_save_leaves_no_temp_file_and_preserves_original_on_failure(self, tmp_path):
        from orb.config.managers.configuration_manager import ConfigurationManager
        from orb.domain.base.exceptions import ConfigurationError

        out = tmp_path / "output.json"
        out.write_text(json.dumps({"original": True}))

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {"new": True}

        # Fail the atomic rename; the original file must stay intact and no
        # temp scratch file should be left behind.
        with patch("os.replace", side_effect=OSError("rename failed")):
            with pytest.raises(ConfigurationError, match="Failed to save"):
                mgr.save(str(out))

        assert json.loads(out.read_text()) == {"original": True}
        assert list(tmp_path.iterdir()) == [out]


# ---------------------------------------------------------------------------
# save() persists the user's OWN config, not the merged/expanded runtime config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSavePersistsOnlyUserConfig:
    """A 'config set' persist must write back the user's original file content
    with the one edit applied — never the loader-merged, env-expanded runtime
    config. Regression guard for the persist path baking in package/strategy
    defaults and replacing ``${VAR}`` placeholders with their plaintext values.
    """

    def test_set_then_save_preserves_placeholders_and_stays_minimal(self, tmp_path, monkeypatch):
        from orb.config.managers.configuration_manager import ConfigurationManager

        # A minimal, hand-authored user config with an env-var placeholder for a
        # secret. The env var is set so the loader WOULD expand it at runtime.
        monkeypatch.setenv("DB_PASSWORD", "super-secret-plaintext")
        user_config = {
            "storage": {"sql_strategy": {"password": "${DB_PASSWORD}"}},
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(user_config))
        original_text = config_path.read_text()

        # Real load path — no mocking of the loader.
        mgr = ConfigurationManager(config_file=str(config_path))
        mgr.set("environment", "staging")
        mgr.save(str(config_path))

        on_disk = json.loads(config_path.read_text())

        # (1) The ${VAR} placeholder must survive verbatim — the plaintext secret
        # must never be written to disk.
        assert on_disk["storage"]["sql_strategy"]["password"] == "${DB_PASSWORD}"
        assert "super-secret-plaintext" not in config_path.read_text()

        # (2) The file must not have gained package/strategy default keys — it
        # stays essentially as minimal as the user authored it (their one key
        # plus the single new key).
        assert set(on_disk.keys()) == {"storage", "environment"}

        # (3) The new set key was applied and persisted.
        assert on_disk["environment"] == "staging"

        # Sanity: without the fix the file would balloon far past the original.
        assert len(config_path.read_text()) < 2 * len(original_text)

    def test_save_from_in_memory_dict_uses_original_not_merged(self, tmp_path):
        from orb.config.managers.configuration_manager import ConfigurationManager

        user_config = {"request": {"default_timeout": 111}}
        mgr = ConfigurationManager(config_dict=user_config)
        mgr.set("request.default_timeout", 222)

        out = tmp_path / "config.json"
        mgr.save(str(out))

        on_disk = json.loads(out.read_text())
        # Only the user's own section is written, with the edit applied — no
        # merged defaults (e.g. no top-level "version", "provider", "logging").
        assert on_disk == {"request": {"default_timeout": 222}}

    def test_set_dict_value_replaces_subtree_on_disk_no_stale_keys(self, tmp_path):
        """``set`` of a dict must REPLACE the on-disk subtree, not deep-merge it.

        ``set`` has replace semantics in memory: setting ``provider`` to a new
        dict drops the old ``providers`` sibling. Persistence must match — a
        deep-merge would retain the stale ``providers`` key on disk, so after a
        reload the in-memory and on-disk states would diverge.
        """
        from orb.config.managers.configuration_manager import ConfigurationManager

        user_config = {
            "provider": {"active_provider": "aws", "providers": ["old-aws-entry"]},
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(user_config))

        mgr = ConfigurationManager(config_file=str(config_path))
        # Replace the whole ``provider`` subtree with a value that omits
        # ``providers`` — the in-memory result no longer has that key.
        mgr.set("provider", {"active_provider": "gcp"})
        in_memory = mgr.get("provider")
        assert in_memory == {"active_provider": "gcp"}

        mgr.save(str(config_path))
        on_disk = json.loads(config_path.read_text())

        # On disk must match in-memory: the stale ``providers`` key is GONE.
        assert on_disk["provider"] == {"active_provider": "gcp"}
        assert "providers" not in on_disk["provider"]
        assert on_disk["provider"] == in_memory

    def test_update_dict_value_still_deep_merges(self, tmp_path):
        """``update`` keeps deep-merge semantics — set-replace must not leak into it.

        ``update`` merges into existing dicts, so a partial update of the
        ``provider`` subtree must preserve sibling keys the update did not
        mention (unlike ``set``, which replaces wholesale).
        """
        from orb.config.managers.configuration_manager import ConfigurationManager

        user_config = {
            "provider": {"active_provider": "aws", "region": "us-east-1"},
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(user_config))

        mgr = ConfigurationManager(config_file=str(config_path))
        mgr.update({"provider": {"active_provider": "gcp"}})
        mgr.save(str(config_path))

        on_disk = json.loads(config_path.read_text())
        # ``region`` survives the deep-merge; only ``active_provider`` changed.
        assert on_disk["provider"] == {"active_provider": "gcp", "region": "us-east-1"}


# ---------------------------------------------------------------------------
# get_raw_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetRawConfig:
    def test_returns_copy_of_raw_config(self):
        from orb.config.managers.configuration_manager import ConfigurationManager

        raw = {"a": 1, "b": {"c": 2}}
        mgr = ConfigurationManager(config_dict=raw)
        mgr._raw_config = raw

        result = mgr.get_raw_config()
        assert result == raw
        # Must be a copy, not the same object
        assert result is not raw


# ---------------------------------------------------------------------------
# resolve_file()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveFile:
    def _make_mgr(self):
        from orb.config.managers.configuration_manager import ConfigurationManager

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {}
        return mgr

    def test_explicit_path_with_directory_returned_directly(self, tmp_path):
        mgr = self._make_mgr()
        explicit = str(tmp_path / "subdir" / "myfile.json")
        result = mgr.resolve_file("config", "default.json", explicit_path=explicit)
        assert result == explicit

    def test_explicit_bare_filename_overrides_filename_param(self, tmp_path):
        mgr = self._make_mgr()
        # explicit_path without a directory component — used as filename
        with patch.object(mgr, "_get_scheduler_directory", return_value=str(tmp_path)):
            result = mgr.resolve_file("config", "original.json", explicit_path="override.json")
        # The filename component should be override.json
        assert result.endswith("override.json")

    def test_falls_back_to_default_dir_when_scheduler_path_missing(self, tmp_path):
        mgr = self._make_mgr()
        default_dir = str(tmp_path / "defaults")
        os.makedirs(default_dir, exist_ok=True)
        with patch.object(mgr, "_get_scheduler_directory", return_value=str(tmp_path)):
            # File does not exist in scheduler dir, so falls back
            result = mgr.resolve_file("config", "cfg.json", default_dir=default_dir)
        assert result == os.path.join(default_dir, "cfg.json")

    def test_returns_scheduler_path_when_file_exists_there(self, tmp_path):
        mgr = self._make_mgr()
        scheduler_dir = tmp_path / "sched"
        scheduler_dir.mkdir()
        (scheduler_dir / "templates.json").write_text("{}")
        with patch.object(mgr, "_get_scheduler_directory", return_value=str(scheduler_dir)):
            result = mgr.resolve_file("config", "templates.json")
        assert result == str(scheduler_dir / "templates.json")


# ---------------------------------------------------------------------------
# _get_scheduler_directory()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSchedulerDirectory:
    def _make_mgr(self):
        from orb.config.managers.configuration_manager import ConfigurationManager

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {}
        return mgr

    def test_config_file_type_returns_config_location(self):
        mgr = self._make_mgr()
        with patch("orb.config.platform_dirs.get_config_location") as mock_loc:
            mock_loc.return_value = Path("/tmp/config")
            result = mgr._get_scheduler_directory("config")
        assert result == "/tmp/config"

    def test_log_file_type_returns_logs_location(self):
        mgr = self._make_mgr()
        with patch("orb.config.platform_dirs.get_logs_location") as mock_logs:
            mock_logs.return_value = Path("/tmp/logs")
            result = mgr._get_scheduler_directory("log")
        assert result == "/tmp/logs"

    def test_work_file_type_returns_work_location(self):
        mgr = self._make_mgr()
        with patch("orb.config.platform_dirs.get_work_location") as mock_work:
            mock_work.return_value = Path("/tmp/work")
            result = mgr._get_scheduler_directory("work")
        assert result == "/tmp/work"

    def test_unknown_file_type_falls_through_to_work_location(self):
        mgr = self._make_mgr()
        with patch("orb.config.platform_dirs.get_work_location") as mock_work:
            mock_work.return_value = Path("/tmp/work")
            result = mgr._get_scheduler_directory("snapshots")
        assert result == "/tmp/work"


# ---------------------------------------------------------------------------
# find_templates_file()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindTemplatesFile:
    def _make_mgr(self):
        from orb.config.managers.configuration_manager import ConfigurationManager

        mgr = ConfigurationManager(config_dict={})
        mgr._raw_config = {}
        return mgr

    def test_finds_provider_specific_template_file(self, tmp_path):
        mgr = self._make_mgr()
        (tmp_path / "awsprov_templates.json").write_text("{}")

        with patch.object(mgr, "_get_scheduler_directory", return_value=str(tmp_path)):
            result = mgr.find_templates_file("aws")

        assert result == str(tmp_path / "awsprov_templates.json")

    def test_finds_generic_templates_file_for_default_provider(self, tmp_path):
        mgr = self._make_mgr()
        (tmp_path / "templates.json").write_text("{}")

        with patch.object(mgr, "_get_scheduler_directory", return_value=str(tmp_path)):
            result = mgr.find_templates_file("default")

        assert result == str(tmp_path / "templates.json")

    def test_falls_back_to_generic_template_when_provider_specific_missing(self, tmp_path):
        mgr = self._make_mgr()
        (tmp_path / "templates.json").write_text("{}")
        # No awsprov_templates.json, aws_templates.json, or slurm_aws_templates.json exists

        with (
            patch.object(mgr, "_get_scheduler_directory", return_value=str(tmp_path)),
            patch.object(mgr, "resolve_path", return_value=str(tmp_path)),
        ):
            result = mgr.find_templates_file("aws")

        assert result == str(tmp_path / "templates.json")

    def test_raises_file_not_found_when_no_templates_exist(self, tmp_path):
        mgr = self._make_mgr()
        # No template files at all

        with (
            patch.object(mgr, "_get_scheduler_directory", return_value=str(tmp_path)),
            patch.object(mgr, "resolve_path", return_value=str(tmp_path)),
        ):
            with pytest.raises(FileNotFoundError, match="Templates file not found"):
                mgr.find_templates_file("aws")

    def test_get_cache_stats_delegates_to_cache_manager(self):
        mgr = self._make_mgr()
        mgr._cache_manager = MagicMock()
        mgr._cache_manager.get_cache_stats.return_value = {"cache_size": 0}
        stats = mgr.get_cache_stats()
        assert stats["cache_size"] == 0
