"""Unit tests for orb.config.services.config_loader_service.

Covers ConfigLoaderService.load_config_file(), _load_from_file(),
load_default_config(), merge_configs(), expand_env_vars(), convert_value(),
and the factory function create_config_loader_service().
All filesystem operations are patched — no real disk I/O.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from orb.config.services.config_loader_service import (
    ConfigLoaderService,
    create_config_loader_service,
)
from orb.domain.base.exceptions import ConfigurationError


def _make_service(resolver: MagicMock | None = None) -> ConfigLoaderService:
    if resolver is None:
        resolver = MagicMock()
    return ConfigLoaderService(resolver)


# ---------------------------------------------------------------------------
# load_config_file
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadConfigFile:
    """load_config_file() — path resolution + file load integration."""

    def test_returns_none_when_resolved_path_is_none(self):
        svc = _make_service()
        svc.path_resolver.resolve_file_path.return_value = None  # type: ignore[union-attr]
        result = svc.load_config_file("config", "config.json")
        assert result is None

    def test_returns_none_when_resolved_path_does_not_exist(self, tmp_path):
        svc = _make_service()
        nonexistent = str(tmp_path / "missing.json")
        svc.path_resolver.resolve_file_path.return_value = nonexistent  # type: ignore[union-attr]
        result = svc.load_config_file("config", "missing.json")
        assert result is None

    def test_loads_valid_json_from_existing_file(self, tmp_path):
        svc = _make_service()
        cfg_file = tmp_path / "config.json"
        data = {"key": "value", "nested": {"a": 1}}
        cfg_file.write_text(json.dumps(data))
        svc.path_resolver.resolve_file_path.return_value = str(cfg_file)  # type: ignore[union-attr]
        result = svc.load_config_file("config", "config.json")
        assert result == data

    def test_required_missing_still_returns_none(self, tmp_path):
        """required=True only changes log level; still returns None when missing."""
        svc = _make_service()
        svc.path_resolver.resolve_file_path.return_value = None  # type: ignore[union-attr]
        result = svc.load_config_file("config", "required.json", required=True)
        assert result is None

    def test_explicit_path_forwarded_to_resolver(self, tmp_path):
        svc = _make_service()
        explicit = str(tmp_path / "explicit.json")
        svc.path_resolver.resolve_file_path.return_value = None  # type: ignore[union-attr]
        svc.load_config_file("config", "config.json", explicit_path=explicit)
        svc.path_resolver.resolve_file_path.assert_called_once_with(  # type: ignore[union-attr]
            "config", "config.json", explicit
        )


# ---------------------------------------------------------------------------
# _load_from_file
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadFromFile:
    """_load_from_file() — direct file read path."""

    def test_returns_none_for_nonexistent_path(self, tmp_path):
        svc = _make_service()
        result = svc._load_from_file(str(tmp_path / "ghost.json"))
        assert result is None

    def test_parses_valid_json(self, tmp_path):
        svc = _make_service()
        p = tmp_path / "c.json"
        p.write_text('{"hello": 42}')
        result = svc._load_from_file(str(p))
        assert result == {"hello": 42}

    def test_invalid_json_raises_configuration_error(self, tmp_path):
        svc = _make_service()
        p = tmp_path / "bad.json"
        p.write_text("NOT JSON")
        with pytest.raises(ConfigurationError) as exc_info:
            svc._load_from_file(str(p))
        # ConfigurationError stores detail in error_code (2nd positional arg)
        assert "Invalid JSON" in exc_info.value.error_code

    def test_read_permission_error_raises_configuration_error(self, tmp_path):
        svc = _make_service()
        p = tmp_path / "locked.json"
        p.write_text('{"x": 1}')
        # _load_from_file uses Path.open(), not builtins.open directly
        with patch("pathlib.Path.open", side_effect=PermissionError("denied")):
            with pytest.raises(ConfigurationError) as exc_info:
                svc._load_from_file(str(p))
        assert "Failed to load" in exc_info.value.error_code


# ---------------------------------------------------------------------------
# load_default_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadDefaultConfig:
    """load_default_config() — platform_dirs path + error handling."""

    def test_returns_empty_dict_when_default_config_missing(self, tmp_path):
        svc = _make_service()
        with patch(
            "orb.config.platform_dirs.get_config_location",
            return_value=tmp_path,
        ):
            result = svc.load_default_config()
        assert result == {}

    def test_loads_default_config_when_present(self, tmp_path):
        svc = _make_service()
        default_file = tmp_path / "default_config.json"
        default_file.write_text('{"version": "test"}')
        with patch(
            "orb.config.platform_dirs.get_config_location",
            return_value=tmp_path,
        ):
            result = svc.load_default_config()
        assert result == {"version": "test"}

    def test_returns_empty_dict_on_exception(self):
        svc = _make_service()
        with patch(
            "orb.config.platform_dirs.get_config_location",
            side_effect=RuntimeError("platform error"),
        ):
            result = svc.load_default_config()
        assert result == {}


# ---------------------------------------------------------------------------
# merge_configs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMergeConfigs:
    """merge_configs() — deep merge semantics."""

    def test_shallow_key_replaced(self):
        svc = _make_service()
        base = {"key": "old"}
        svc.merge_configs(base, {"key": "new"})
        assert base["key"] == "new"

    def test_new_key_added(self):
        svc = _make_service()
        base = {"existing": 1}
        svc.merge_configs(base, {"added": 2})
        assert base["added"] == 2
        assert base["existing"] == 1

    def test_nested_dict_deep_merged(self):
        svc = _make_service()
        base = {"a": {"x": 1, "y": 2}}
        svc.merge_configs(base, {"a": {"y": 99, "z": 3}})
        assert base["a"] == {"x": 1, "y": 99, "z": 3}

    def test_list_value_replaced_not_merged(self):
        svc = _make_service()
        base = {"items": [1, 2, 3]}
        svc.merge_configs(base, {"items": [4, 5]})
        assert base["items"] == [4, 5]

    def test_empty_update_leaves_base_unchanged(self):
        svc = _make_service()
        base = {"k": "v"}
        svc.merge_configs(base, {})
        assert base == {"k": "v"}

    def test_recursive_deep_merge_multiple_levels(self):
        svc = _make_service()
        base = {"a": {"b": {"c": 1}}}
        svc.merge_configs(base, {"a": {"b": {"d": 2}}})
        assert base == {"a": {"b": {"c": 1, "d": 2}}}


# ---------------------------------------------------------------------------
# expand_env_vars
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExpandEnvVars:
    """expand_env_vars() — delegates to env_expansion util."""

    def test_delegates_to_expand_config_env_vars(self):
        svc = _make_service()
        cfg = {"url": "${MY_HOST}:8080"}
        expanded = {"url": "localhost:8080"}
        with patch(
            "orb.config.utils.env_expansion.expand_config_env_vars",
            return_value=expanded,
        ) as mock_expand:
            result = svc.expand_env_vars(cfg)
        mock_expand.assert_called_once_with(cfg)
        assert result == expanded


# ---------------------------------------------------------------------------
# convert_value
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConvertValue:
    """convert_value() — type coercion for string values."""

    def test_true_string_converts_to_bool_true(self):
        svc = _make_service()
        assert svc.convert_value("true") is True

    def test_True_string_converts_to_bool_true(self):
        svc = _make_service()
        assert svc.convert_value("True") is True

    def test_false_string_converts_to_bool_false(self):
        svc = _make_service()
        assert svc.convert_value("false") is False

    def test_integer_string_converts_to_int(self):
        svc = _make_service()
        assert svc.convert_value("42") == 42
        assert isinstance(svc.convert_value("42"), int)

    def test_float_string_converts_to_float(self):
        svc = _make_service()
        result = svc.convert_value("3.14")
        assert isinstance(result, float)
        assert abs(result - 3.14) < 1e-9

    def test_json_list_string_converts_to_list(self):
        svc = _make_service()
        result = svc.convert_value("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_json_dict_string_converts_to_dict(self):
        svc = _make_service()
        result = svc.convert_value('{"a": 1}')
        assert result == {"a": 1}

    def test_plain_string_returned_as_is(self):
        svc = _make_service()
        assert svc.convert_value("hello world") == "hello world"

    def test_empty_string_returned_as_is(self):
        svc = _make_service()
        assert svc.convert_value("") == ""


# ---------------------------------------------------------------------------
# create_config_loader_service factory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateConfigLoaderServiceFactory:
    def test_returns_config_loader_service_instance(self):
        resolver = MagicMock()
        svc = create_config_loader_service(resolver)
        assert isinstance(svc, ConfigLoaderService)
        assert svc.path_resolver is resolver
