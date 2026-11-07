"""Unreadable or invalid templates files are reported, not loaded as empty."""

from __future__ import annotations

import asyncio
import builtins
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.template.configuration_manager import (
    TemplateConfigurationError,
    TemplateConfigurationManager,
)
from orb.infrastructure.template.template_cache_service import create_template_cache_service

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, content: Any, name: str = "templates.json") -> str:
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return str(path)


def _default_strategy(on_mismatch: str = "warn") -> DefaultSchedulerStrategy:
    strategy = DefaultSchedulerStrategy()
    strategy._logger = MagicMock()
    config = MagicMock()
    config.app_config.scheduler.on_scheduler_mismatch = on_mismatch
    strategy._config_manager = config
    return strategy


def _manager(strategy: Any, paths: list[str]) -> TemplateConfigurationManager:
    logger = MagicMock()
    config = MagicMock()
    config.get_provider_config.return_value = None
    strategy.get_template_paths = MagicMock(return_value=paths)
    return TemplateConfigurationManager(
        config_manager=config,
        scheduler_strategy=strategy,
        logger=logger,
        cache_service=create_template_cache_service("noop", logger),
    )


def _open_raising_for(target: str, exc: Exception):
    real_open = builtins.open

    def _open(file, *args, **kwargs):
        if str(file) == target:
            raise exc
        return real_open(file, *args, **kwargs)

    return _open


_VALID = {"templates": [{"template_id": "t1", "provider_api": "EC2Fleet", "max_instances": 1}]}


# ---------------------------------------------------------------------------
# Strategy level
# ---------------------------------------------------------------------------


class TestDefaultStrategyLoadFailures:
    def test_unreadable_file_raises(self, tmp_path):
        path = _write(tmp_path, _VALID)
        strategy = _default_strategy()
        with patch("builtins.open", _open_raising_for(path, PermissionError("denied"))):
            with pytest.raises(TemplateConfigurationError) as info:
                strategy.load_templates_from_path(path)
        assert path in str(info.value)
        assert info.value.details == {"path": path}
        assert isinstance(info.value.__cause__, PermissionError)

    def test_invalid_json_raises(self, tmp_path):
        path = _write(tmp_path, "{not valid json")
        with pytest.raises(TemplateConfigurationError):
            _default_strategy().load_templates_from_path(path)

    def test_templates_not_a_list_raises(self, tmp_path):
        path = _write(tmp_path, {"templates": "x"})
        with pytest.raises(TemplateConfigurationError, match="must be a list"):
            _default_strategy().load_templates_from_path(path)

    def test_scalar_top_level_raises(self, tmp_path):
        path = _write(tmp_path, "42")
        with pytest.raises(TemplateConfigurationError):
            _default_strategy().load_templates_from_path(path)

    def test_dict_without_templates_key_is_empty_with_warning(self, tmp_path):
        path = _write(tmp_path, {"foo": []})
        strategy = _default_strategy()
        assert strategy.load_templates_from_path(path) == []
        logger: Any = strategy._logger
        warnings = " ".join(str(c) for c in logger.warning.call_args_list)
        assert path in warnings

    def test_missing_file_is_empty(self, tmp_path):
        assert _default_strategy().load_templates_from_path(str(tmp_path / "nope.json")) == []

    def test_file_removed_after_exists_check_is_empty(self, tmp_path):
        path = _write(tmp_path, _VALID)
        strategy = _default_strategy()
        with patch("builtins.open", _open_raising_for(path, FileNotFoundError(path))):
            assert strategy.load_templates_from_path(path) == []

    def test_template_configuration_error_is_a_configuration_error(self):
        assert issubclass(TemplateConfigurationError, ConfigurationError)


class TestHostFactoryStrategyLoadFailures:
    def test_invalid_json_raises(self, tmp_path):
        path = _write(tmp_path, "{not valid json")
        strategy = HostFactorySchedulerStrategy()
        strategy._logger = MagicMock()
        with pytest.raises(TemplateConfigurationError):
            strategy.load_templates_from_path(path)

    def test_skipped_row_is_logged_with_its_template_id(self, tmp_path):
        path = _write(tmp_path, {"templates": [{"templateId": "hf-bad"}]})
        strategy = HostFactorySchedulerStrategy()
        strategy._logger = MagicMock()
        with patch.object(strategy, "_map_template_fields", side_effect=ValueError("bad row")):
            assert strategy.load_templates_from_path(path) == []
        assert "hf-bad" in str(strategy._logger.warning.call_args_list)


class TestDelegatedLoadFailures:
    def test_delegate_error_reaches_the_manager(self, tmp_path):
        path = _write(tmp_path, {"scheduler_type": "hostfactory", "templates": "x"})
        strategy = _default_strategy()
        registry = MagicMock()
        registry.is_registered.return_value = True
        registry.get_strategy_class.return_value = HostFactorySchedulerStrategy
        mgr = _manager(strategy, [path])

        with patch(
            "orb.infrastructure.scheduler.registry.get_scheduler_registry",
            return_value=registry,
        ):
            with pytest.raises(TemplateConfigurationError):
                asyncio.run(mgr.load_templates())

    def test_unregistered_scheduler_with_mismatch_fail_raises(self, tmp_path):
        path = _write(tmp_path, {"scheduler_type": "not-a-scheduler", "templates": []})
        strategy = _default_strategy(on_mismatch="fail")
        registry = MagicMock()
        registry.is_registered.return_value = False

        with patch(
            "orb.infrastructure.scheduler.registry.get_scheduler_registry",
            return_value=registry,
        ):
            with pytest.raises(TemplateConfigurationError, match="on_scheduler_mismatch=fail"):
                strategy.load_templates_from_path(path)


# ---------------------------------------------------------------------------
# Manager level
# ---------------------------------------------------------------------------


class TestManagerLoadFailures:
    def test_invalid_json_file_raises_from_load_templates(self, tmp_path):
        good = _write(tmp_path, _VALID, "good.json")
        bad = _write(tmp_path, "{not valid json", "bad.json")
        mgr = _manager(_default_strategy(), [good, bad])
        with pytest.raises(TemplateConfigurationError):
            asyncio.run(mgr.load_templates())

    def test_unreadable_file_raises_from_load_templates(self, tmp_path):
        path = _write(tmp_path, _VALID)
        mgr = _manager(_default_strategy(), [path])
        with patch("builtins.open", _open_raising_for(path, PermissionError("denied"))):
            with pytest.raises(TemplateConfigurationError):
                asyncio.run(mgr.load_templates())

    def test_templates_not_a_list_raises_from_load_templates(self, tmp_path):
        path = _write(tmp_path, {"templates": "x"})
        mgr = _manager(_default_strategy(), [path])
        with pytest.raises(TemplateConfigurationError):
            asyncio.run(mgr.load_templates())

    def test_dict_without_templates_key_loads_empty(self, tmp_path):
        path = _write(tmp_path, {"foo": []})
        mgr = _manager(_default_strategy(), [path])
        assert asyncio.run(mgr.load_templates()) == []

    def test_one_invalid_template_is_logged_with_id_and_others_kept(self, tmp_path):
        path = _write(
            tmp_path,
            {
                "templates": [
                    {"template_id": "good", "provider_api": "EC2Fleet", "max_instances": 1},
                    {"template_id": "broken", "provider_api": "EC2Fleet", "max_instances": 1},
                ]
            },
        )
        mgr = _manager(_default_strategy(), [path])
        real_convert = mgr._convert_dict_to_template_dto

        def _convert(template_dict, *args, **kwargs):
            if template_dict.get("template_id") == "broken":
                raise ValueError("cannot convert")
            return real_convert(template_dict, *args, **kwargs)

        with patch.object(mgr, "_convert_dict_to_template_dto", side_effect=_convert):
            templates = asyncio.run(mgr.load_templates())

        assert [t.template_id for t in templates] == ["good"]
        logger: Any = mgr.logger
        errors = str(logger.error.call_args_list)
        assert "broken" in errors


class TestManagerSyncPathLoadFailures:
    """The sync loader runs when an event loop is already running."""

    @pytest.mark.asyncio
    async def test_get_template_raises_on_invalid_file(self, tmp_path):
        path = _write(tmp_path, "{not valid json")
        mgr = _manager(_default_strategy(), [path])
        with pytest.raises(TemplateConfigurationError):
            mgr.get_template("t1")

    @pytest.mark.asyncio
    async def test_get_all_templates_sync_raises_on_invalid_file(self, tmp_path):
        path = _write(tmp_path, "{not valid json")
        mgr = _manager(_default_strategy(), [path])
        with pytest.raises(TemplateConfigurationError):
            mgr.get_all_templates_sync()

    @pytest.mark.asyncio
    async def test_get_template_finds_template_in_valid_file(self, tmp_path):
        path = _write(tmp_path, _VALID)
        mgr = _manager(_default_strategy(), [path])
        template = mgr.get_template("t1")
        assert template is not None
        assert template.template_id == "t1"
