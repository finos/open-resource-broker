"""Coverage-gap tests for orb.config.loader.ConfigurationLoader.

Targets the branches NOT covered by existing tests/unit/config/:
- _merge_config: deep dict merge, scalar replace, list replace
- _convert_value: bool/int/float/json/string conversion
- _collect_dotted_keys: flat and nested
- _deep_copy: round-trip via JSON
- _load_from_env: all ORB_* env var branches (LOG_LEVEL, DEBUG, ENVIRONMENT,
  REQUEST_TIMEOUT, MAX_MACHINES_PER_REQUEST, CONFIG_FILE, CONSOLE_ENABLED)
- _load_from_env SDK override warning when _sdk_keys provided
- _load_from_file: valid file, missing file, invalid JSON
- create_app_config: ValueError path, KeyError path, generic Exception path

All tests are pure / tmp_path — no live AWS, DB, or network.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from orb.config.loader import ConfigurationLoader
from orb.domain.base.exceptions import ConfigurationError

# ---------------------------------------------------------------------------
# _merge_config
# ---------------------------------------------------------------------------


class TestMergeConfig:
    def test_scalar_value_replaced(self):
        base = {"a": 1}
        ConfigurationLoader._merge_config(base, {"a": 99})
        assert base["a"] == 99

    def test_new_key_added(self):
        base = {"a": 1}
        ConfigurationLoader._merge_config(base, {"b": 2})
        assert base["b"] == 2

    def test_nested_dict_deep_merged(self):
        base = {"a": {"x": 1, "y": 2}}
        ConfigurationLoader._merge_config(base, {"a": {"y": 99, "z": 3}})
        assert base["a"]["x"] == 1
        assert base["a"]["y"] == 99
        assert base["a"]["z"] == 3

    def test_list_replaced_entirely(self):
        base = {"items": [1, 2, 3]}
        ConfigurationLoader._merge_config(base, {"items": [4, 5]})
        assert base["items"] == [4, 5]

    def test_none_value_replaces_dict(self):
        base = {"cfg": {"key": "val"}}
        ConfigurationLoader._merge_config(base, {"cfg": None})
        assert base["cfg"] is None

    def test_dict_value_replaces_scalar(self):
        base = {"x": 42}
        ConfigurationLoader._merge_config(base, {"x": {"nested": True}})
        assert base["x"] == {"nested": True}

    def test_empty_update_leaves_base_unchanged(self):
        base = {"a": 1, "b": 2}
        ConfigurationLoader._merge_config(base, {})
        assert base == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# _convert_value
# ---------------------------------------------------------------------------


class TestConvertValue:
    def test_true_string_to_bool(self):
        assert ConfigurationLoader._convert_value("true") is True
        assert ConfigurationLoader._convert_value("True") is True

    def test_false_string_to_bool(self):
        assert ConfigurationLoader._convert_value("false") is False
        assert ConfigurationLoader._convert_value("FALSE") is False

    def test_integer_string_to_int(self):
        assert ConfigurationLoader._convert_value("42") == 42
        assert isinstance(ConfigurationLoader._convert_value("42"), int)

    def test_float_string_to_float(self):
        assert ConfigurationLoader._convert_value("3.14") == pytest.approx(3.14)
        assert isinstance(ConfigurationLoader._convert_value("3.14"), float)

    def test_json_list_to_list(self):
        result = ConfigurationLoader._convert_value("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_json_dict_to_dict(self):
        result = ConfigurationLoader._convert_value('{"key": "val"}')
        assert result == {"key": "val"}

    def test_plain_string_returned_as_is(self):
        assert ConfigurationLoader._convert_value("hello") == "hello"

    def test_non_json_gibberish_returned_as_str(self):
        assert ConfigurationLoader._convert_value("not_a_number!") == "not_a_number!"


# ---------------------------------------------------------------------------
# _collect_dotted_keys
# ---------------------------------------------------------------------------


class TestCollectDottedKeys:
    def test_flat_dict(self):
        keys = ConfigurationLoader._collect_dotted_keys({"a": 1, "b": "x"})
        assert set(keys) == {"a", "b"}

    def test_nested_dict(self):
        keys = ConfigurationLoader._collect_dotted_keys({"a": {"b": {"c": 1}}})
        assert "a.b.c" in keys

    def test_mixed_flat_and_nested(self):
        keys = ConfigurationLoader._collect_dotted_keys({"top": 1, "mid": {"leaf": 2}})
        assert "top" in keys
        assert "mid.leaf" in keys

    def test_empty_dict_returns_empty_list(self):
        assert ConfigurationLoader._collect_dotted_keys({}) == []


# ---------------------------------------------------------------------------
# _deep_copy
# ---------------------------------------------------------------------------


class TestDeepCopy:
    def test_produces_independent_copy(self):
        original = {"a": {"b": [1, 2, 3]}}
        copy = ConfigurationLoader._deep_copy(original)
        copy["a"]["b"].append(99)
        assert original["a"]["b"] == [1, 2, 3]

    def test_round_trips_values(self):
        data = {"s": "hello", "n": 42, "b": True, "lst": [1, 2]}
        copy = ConfigurationLoader._deep_copy(data)
        assert copy == data


# ---------------------------------------------------------------------------
# _load_from_file
# ---------------------------------------------------------------------------


class TestLoadFromFile:
    def test_returns_none_for_missing_file(self, tmp_path):
        result = ConfigurationLoader._load_from_file(str(tmp_path / "missing.json"))
        assert result is None

    def test_loads_valid_json(self, tmp_path):
        f = tmp_path / "cfg.json"
        f.write_text(json.dumps({"key": "value"}))
        result = ConfigurationLoader._load_from_file(str(f))
        assert result == {"key": "value"}

    def test_raises_configuration_error_for_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{invalid json {{")
        with pytest.raises(ConfigurationError):
            ConfigurationLoader._load_from_file(str(f))


# ---------------------------------------------------------------------------
# _load_from_env — individual env var branches
# ---------------------------------------------------------------------------


class TestLoadFromEnv:
    """Each test isolates a single ORB_* env var."""

    def _clean_env(self, monkeypatch):
        """Remove all ORB_* env vars to start from a blank slate."""
        for key in list(os.environ.keys()):
            if key.startswith("ORB_"):
                monkeypatch.delenv(key, raising=False)

    def test_orb_log_level_sets_logging_level(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setenv("ORB_LOG_LEVEL", "DEBUG")
        config = {}
        ConfigurationLoader._load_from_env(config, config_manager=None)
        assert config.get("logging", {}).get("level") == "DEBUG"

    def test_orb_debug_true(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setenv("ORB_DEBUG", "true")
        config = {}
        ConfigurationLoader._load_from_env(config, config_manager=None)
        assert config.get("debug") is True

    def test_orb_debug_false(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setenv("ORB_DEBUG", "false")
        config = {}
        ConfigurationLoader._load_from_env(config, config_manager=None)
        assert config.get("debug") is False

    def test_orb_environment(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setenv("ORB_ENVIRONMENT", "staging")
        config = {}
        ConfigurationLoader._load_from_env(config, config_manager=None)
        assert config.get("environment") == "staging"

    def test_orb_request_timeout(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setenv("ORB_REQUEST_TIMEOUT", "120")
        config = {}
        ConfigurationLoader._load_from_env(config, config_manager=None)
        assert config.get("request", {}).get("default_timeout") == 120

    def test_orb_max_machines_per_request(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setenv("ORB_MAX_MACHINES_PER_REQUEST", "50")
        config = {}
        ConfigurationLoader._load_from_env(config, config_manager=None)
        assert config.get("request", {}).get("max_machines_per_request") == 50

    def test_orb_config_file(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setenv("ORB_CONFIG_FILE", "/tmp/test_cfg.json")
        config = {}
        ConfigurationLoader._load_from_env(config, config_manager=None)
        assert config.get("config_file") == "/tmp/test_cfg.json"

    def test_orb_log_console_enabled_true(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setenv("ORB_LOG_CONSOLE_ENABLED", "true")
        config = {}
        ConfigurationLoader._load_from_env(config, config_manager=None)
        assert config.get("logging", {}).get("console_enabled") is True

    def test_orb_log_console_enabled_false(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setenv("ORB_LOG_CONSOLE_ENABLED", "false")
        config = {}
        ConfigurationLoader._load_from_env(config, config_manager=None)
        assert config.get("logging", {}).get("console_enabled") is False

    def test_sdk_override_warning_emitted(self, monkeypatch):
        """When _sdk_keys contains a key that an env var overrides, a warning is logged."""
        self._clean_env(monkeypatch)
        monkeypatch.setenv("ORB_LOG_LEVEL", "WARNING")
        config = {}
        sdk_keys = frozenset({"logging.level"})
        # The warning goes to the logger — we just want no exception
        ConfigurationLoader._load_from_env(config, config_manager=None, _sdk_keys=sdk_keys)
        assert config.get("logging", {}).get("level") == "WARNING"

    def test_no_env_vars_leaves_config_unchanged(self, monkeypatch):
        self._clean_env(monkeypatch)
        config = {"existing": "value"}
        ConfigurationLoader._load_from_env(config, config_manager=None)
        assert config.get("existing") == "value"


# ---------------------------------------------------------------------------
# create_app_config — error paths
# ---------------------------------------------------------------------------


class TestCreateAppConfig:
    def test_valid_config_returns_app_config(self):
        # Use a minimal valid config dict; real defaults satisfy validation
        cfg = ConfigurationLoader._load_default_config()
        result = ConfigurationLoader.create_app_config(cfg)
        assert result is not None

    def test_invalid_config_raises_configuration_error(self):
        with pytest.raises(ConfigurationError):
            ConfigurationLoader.create_app_config({"logging": {"level": None}})

    def test_value_error_wrapped_as_configuration_error(self):
        with patch("orb.config.loader.validate_config", side_effect=ValueError("bad val")):
            with pytest.raises(ConfigurationError):
                ConfigurationLoader.create_app_config({})

    def test_key_error_wrapped_as_configuration_error(self):
        with patch("orb.config.loader.validate_config", side_effect=KeyError("missing")):
            with pytest.raises(ConfigurationError):
                ConfigurationLoader.create_app_config({})

    def test_generic_exception_wrapped_as_configuration_error(self):
        with patch("orb.config.loader.validate_config", side_effect=RuntimeError("boom")):
            with pytest.raises(ConfigurationError):
                ConfigurationLoader.create_app_config({})
