"""Unit tests for BaseSchedulerStrategy methods not covered by existing tests.

Coverage targets from strategy.py: lines 71-73,84-86,99-101,105,107-108,117,
163-164,181-188,190-193,205,214-217,219-220,228,236,244,253-254,256-257,259-261,
263,270-271,274-275,278,282,285-286,288-289,293,297,301,334-340,343-344,377-378,
380-381,383-384,390,394,398,433-434,439,441,444,448
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_strategy_with_config(
    *,
    config_dir: str | None = None,
    work_dir: str | None = None,
    log_dir: str | None = None,
    log_level: str | None = None,
    on_mismatch: str = "warn",
) -> DefaultSchedulerStrategy:
    """Build a DefaultSchedulerStrategy with a mocked config manager."""
    strategy = DefaultSchedulerStrategy()
    mock_config = MagicMock()
    mock_scheduler = MagicMock()
    mock_scheduler.config_dir = config_dir
    mock_scheduler.work_dir = work_dir
    mock_scheduler.log_dir = log_dir
    mock_scheduler.log_level = log_level
    mock_scheduler.on_scheduler_mismatch = on_mismatch
    mock_config.app_config.scheduler = mock_scheduler
    strategy._config_manager = mock_config
    return strategy


# ---------------------------------------------------------------------------
# logger property
# ---------------------------------------------------------------------------


class TestLoggerProperty:
    def test_returns_injected_logger(self):
        strategy = DefaultSchedulerStrategy()
        mock_logger = MagicMock()
        strategy._logger = mock_logger
        assert strategy.logger is mock_logger

    def test_returns_module_logger_when_not_injected(self):
        strategy = DefaultSchedulerStrategy()
        strategy._logger = None
        logger = strategy.logger
        assert logger is not None

    def test_config_manager_property(self):
        strategy = DefaultSchedulerStrategy()
        mock_cm = MagicMock()
        strategy._config_manager = mock_cm
        assert strategy.config_manager is mock_cm


# ---------------------------------------------------------------------------
# _get_provider_name
# ---------------------------------------------------------------------------


class TestGetProviderName:
    def test_returns_default_when_no_registry(self):
        strategy = DefaultSchedulerStrategy()
        strategy._provider_registry_service = None
        assert strategy._get_provider_name() == "default"

    def test_returns_provider_name_from_registry(self):
        strategy = DefaultSchedulerStrategy()
        mock_reg = MagicMock()
        mock_reg.select_active_provider.return_value.provider_name = "my-provider"
        strategy._provider_registry_service = mock_reg
        assert strategy._get_provider_name() == "my-provider"

    def test_returns_default_on_registry_exception(self):
        strategy = DefaultSchedulerStrategy()
        mock_reg = MagicMock()
        mock_reg.select_active_provider.side_effect = Exception("registry error")
        strategy._provider_registry_service = mock_reg
        assert strategy._get_provider_name() == "default"


# ---------------------------------------------------------------------------
# _get_active_provider_type
# ---------------------------------------------------------------------------


class TestGetActiveProviderType:
    def test_returns_aws_when_no_registry(self):
        from orb.domain.constants import PROVIDER_TYPE_AWS

        strategy = DefaultSchedulerStrategy()
        strategy._provider_registry_service = None
        assert strategy._get_active_provider_type() == PROVIDER_TYPE_AWS

    def test_returns_provider_type_from_registry(self):
        strategy = DefaultSchedulerStrategy()
        mock_reg = MagicMock()
        mock_reg.select_active_provider.return_value.provider_type = "k8s"
        strategy._provider_registry_service = mock_reg
        assert strategy._get_active_provider_type() == "k8s"

    def test_returns_aws_on_exception(self):
        from orb.domain.constants import PROVIDER_TYPE_AWS

        strategy = DefaultSchedulerStrategy()
        mock_reg = MagicMock()
        mock_reg.select_active_provider.side_effect = RuntimeError("boom")
        strategy._provider_registry_service = mock_reg
        assert strategy._get_active_provider_type() == PROVIDER_TYPE_AWS


# ---------------------------------------------------------------------------
# _load_single_file
# ---------------------------------------------------------------------------


class TestLoadSingleFile:
    def test_loads_templates_list_from_wrapper_dict(self, tmp_path):
        import json

        f = tmp_path / "templates.json"
        f.write_text(json.dumps({"templates": [{"template_id": "t1"}, {"template_id": "t2"}]}))
        strategy = DefaultSchedulerStrategy()
        result = strategy._load_single_file(str(f))
        assert len(result) == 2

    def test_loads_bare_list(self, tmp_path):
        import json

        f = tmp_path / "templates.json"
        f.write_text(json.dumps([{"template_id": "t1"}]))
        strategy = DefaultSchedulerStrategy()
        result = strategy._load_single_file(str(f))
        assert len(result) == 1

    def test_returns_empty_for_nonexistent_file(self):
        strategy = DefaultSchedulerStrategy()
        result = strategy._load_single_file("/no/such/file.json")
        assert result == []

    def test_returns_empty_for_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{{{invalid json")
        strategy = DefaultSchedulerStrategy()
        result = strategy._load_single_file(str(f))
        assert result == []

    def test_returns_empty_when_dict_lacks_templates_key(self, tmp_path):
        import json

        f = tmp_path / "t.json"
        f.write_text(json.dumps({"other": "stuff"}))
        strategy = DefaultSchedulerStrategy()
        result = strategy._load_single_file(str(f))
        assert result == []


# ---------------------------------------------------------------------------
# get_storage_base_path / get_exit_code_for_status
# ---------------------------------------------------------------------------


class TestStorageAndExitCode:
    def test_get_storage_base_path_appends_data(self, tmp_path):
        strategy = make_strategy_with_config(work_dir=str(tmp_path))
        result = strategy.get_storage_base_path()
        assert result.endswith("data")

    def test_exit_code_zero_for_success(self):
        strategy = DefaultSchedulerStrategy()
        assert strategy.get_exit_code_for_status("complete") == 0
        assert strategy.get_exit_code_for_status("pending") == 0
        assert strategy.get_exit_code_for_status("in_progress") == 0

    def test_exit_code_one_for_problem_statuses(self):
        strategy = DefaultSchedulerStrategy()
        for status in ("failed", "cancelled", "timeout", "partial"):
            assert strategy.get_exit_code_for_status(status) == 1


# ---------------------------------------------------------------------------
# format_* methods
# ---------------------------------------------------------------------------


class TestFormatMethods:
    def test_format_template_mutation_response(self):
        strategy = DefaultSchedulerStrategy()
        raw = {"template_id": "t1", "status": "success", "validation_errors": []}
        result = strategy.format_template_mutation_response(raw)
        assert result["template_id"] == "t1"
        assert result["status"] == "success"
        assert result["validation_errors"] == []

    def test_format_health_response_all_pass(self):
        strategy = DefaultSchedulerStrategy()
        checks = [
            {"name": "db", "status": "pass"},
            {"name": "cache", "status": "pass"},
        ]
        result = strategy.format_health_response(checks)
        assert result["success"] is True
        assert result["summary"]["passed"] == 2
        assert result["summary"]["failed"] == 0

    def test_format_health_response_with_failures(self):
        strategy = DefaultSchedulerStrategy()
        checks = [
            {"name": "db", "status": "pass"},
            {"name": "cache", "status": "fail"},
        ]
        result = strategy.format_health_response(checks)
        assert result["success"] is False
        assert result["summary"]["passed"] == 1
        assert result["summary"]["failed"] == 1

    def test_format_system_status_response(self):
        strategy = DefaultSchedulerStrategy()
        raw = {
            "status": "healthy",
            "uptime_seconds": 1000,
            "version": "1.0.0",
            "environment": "prod",
            "active_connections": 5,
            "memory_usage_mb": 256,
            "cpu_usage_percent": 10.5,
            "disk_usage_percent": 30.0,
            "last_health_check": "2026-01-01T00:00:00",
            "components": {"db": "ok"},
        }
        result = strategy.format_system_status_response(raw)
        assert result["status"] == "healthy"
        assert result["uptime_seconds"] == 1000
        assert result["components"] == {"db": "ok"}

    def test_format_provider_detail_response_basic(self):
        strategy = DefaultSchedulerStrategy()
        raw = {"name": "my-prov", "type": "aws", "enabled": True, "config": {}}
        result = strategy.format_provider_detail_response(raw)
        assert result["name"] == "my-prov"
        assert result["type"] == "aws"
        assert "template_defaults" not in result

    def test_format_provider_detail_response_with_template_defaults(self):
        strategy = DefaultSchedulerStrategy()
        raw = {
            "name": "prov",
            "type": "aws",
            "enabled": True,
            "config": {},
            "template_defaults": {"subnet_ids": ["s-1"]},
        }
        result = strategy.format_provider_detail_response(raw)
        assert "template_defaults" in result

    def test_format_storage_test_response_success(self):
        strategy = DefaultSchedulerStrategy()
        raw = {"status": "success", "details": "all good"}
        result = strategy.format_storage_test_response(raw)
        assert result["success"] is True
        assert "successfully" in result["message"]

    def test_format_storage_test_response_success_via_bool(self):
        strategy = DefaultSchedulerStrategy()
        raw = {"success": True, "status": "ok"}
        result = strategy.format_storage_test_response(raw)
        assert result["success"] is True

    def test_format_storage_test_response_failure(self):
        strategy = DefaultSchedulerStrategy()
        raw = {"status": "error"}
        result = strategy.format_storage_test_response(raw)
        assert result["success"] is False
        assert "failed" in result["message"]

    def test_format_return_requests_response_from_dict(self):
        strategy = DefaultSchedulerStrategy()
        requests = [
            {
                "request_id": "ret-001",
                "status": "complete",
                "message": "done",
                "grace_period": 300,
                "machines": [{"machine_id": "m1", "name": "host1"}],
            }
        ]
        result = strategy.format_return_requests_response(requests)
        assert "return_requests" in result
        assert len(result["return_requests"]) == 1
        r = result["return_requests"][0]
        assert r["request_id"] == "ret-001"
        assert len(r["machines"]) == 1

    def test_format_return_requests_response_from_object_with_to_dict(self):
        strategy = DefaultSchedulerStrategy()
        obj = MagicMock()
        obj.to_dict.return_value = {
            "request_id": "ret-002",
            "status": "pending",
            "machines": [],
        }
        result = strategy.format_return_requests_response([obj])
        assert result["return_requests"][0]["request_id"] == "ret-002"

    def test_format_return_requests_response_from_object_with_model_dump(self):
        strategy = DefaultSchedulerStrategy()
        obj = MagicMock(spec=["model_dump"])
        obj.model_dump.return_value = {
            "request_id": "ret-003",
            "status": "complete",
            "machine_references": [{"machine_id": "m2", "name": "host2"}],
        }
        result = strategy.format_return_requests_response([obj])
        assert result["return_requests"][0]["machines"][0]["machine_id"] == "m2"

    def test_format_request_status_response_from_dto_list(self):
        strategy = DefaultSchedulerStrategy()
        dto = MagicMock()
        dto.to_dict.return_value = {"request_id": "req-001", "status": "complete"}
        result = strategy.format_request_status_response([dto])
        assert result["count"] == 1
        assert result["requests"][0]["request_id"] == "req-001"

    def test_format_request_status_response_from_dict_list(self):
        strategy = DefaultSchedulerStrategy()
        result = strategy.format_request_status_response([{"request_id": "req-002"}])
        assert result["requests"][0]["request_id"] == "req-002"


# ---------------------------------------------------------------------------
# Directory resolution (get_config_directory, get_working_directory, etc.)
# ---------------------------------------------------------------------------


class TestDirectoryResolution:
    def test_get_config_directory_uses_config_override(self):
        strategy = make_strategy_with_config(config_dir="/my/config")
        result = strategy.get_config_directory()
        assert result == "/my/config"

    def test_get_working_directory_uses_work_dir_override(self):
        strategy = make_strategy_with_config(work_dir="/my/work")
        result = strategy.get_working_directory()
        assert result == "/my/work"

    def test_get_logs_directory_uses_log_dir_override(self):
        strategy = make_strategy_with_config(log_dir="/my/logs")
        result = strategy.get_logs_directory()
        assert result == "/my/logs"

    def test_get_config_directory_falls_through_to_path_resolver(self):
        strategy = make_strategy_with_config(config_dir=None)
        # None config_dir → try env var → fall to path resolver
        mock_resolver = MagicMock()
        mock_resolver.get_config_dir.return_value = "/platform/config"
        strategy._path_resolver = mock_resolver

        with patch.dict("os.environ", {}, clear=False):
            # Ensure no HF_CONFIG_DIR env var set
            import os

            os.environ.pop("HF_CONFIG_DIR", None)
            os.environ.pop("CONFIG_DIR", None)
            result = strategy.get_config_directory()
        assert result == "/platform/config"

    def test_get_log_level_uses_config_override(self):
        strategy = make_strategy_with_config(log_level="DEBUG")
        result = strategy.get_log_level()
        assert result == "DEBUG"

    def test_get_log_level_falls_back_to_info(self):
        strategy = DefaultSchedulerStrategy()
        # Set _config_manager to a mock that returns None for log_level
        mock_cm = MagicMock()
        mock_cm.app_config.scheduler.log_level = None
        mock_cm.get_logging_config.return_value = {"level": None}
        strategy._config_manager = mock_cm
        result = strategy.get_log_level()
        assert result == "INFO"

    def test_get_log_level_uses_logging_config(self):
        strategy = DefaultSchedulerStrategy()
        mock_cm = MagicMock()
        mock_cm.app_config.scheduler.log_level = None
        mock_cm.get_logging_config.return_value = {"level": "WARNING"}
        strategy._config_manager = mock_cm
        result = strategy.get_log_level()
        assert result == "WARNING"

    def test_coalesce_directory_prefers_env_var(self):
        strategy = DefaultSchedulerStrategy()
        strategy._config_manager = None

        mock_resolver = MagicMock()
        mock_resolver.get_config_dir.return_value = "/default_dir"
        strategy._path_resolver = mock_resolver

        # Override _get_scheduler_env_var to return the env value
        with patch.object(strategy, "_get_scheduler_env_var", return_value="/env/dir"):
            result = strategy._coalesce_directory(
                config_override=None,
                env_var_name="CONFIG_DIR",
                default_factory=lambda: "/default",
            )
        assert result == "/env/dir"

    def test_coalesce_directory_uses_default_factory_when_no_env(self):
        strategy = DefaultSchedulerStrategy()
        with patch.object(strategy, "_get_scheduler_env_var", return_value=None):
            result = strategy._coalesce_directory(
                config_override=None,
                env_var_name="SOME_DIR",
                default_factory=lambda: "/default_from_factory",
            )
        assert result == "/default_from_factory"


# ---------------------------------------------------------------------------
# get_templates_filename
# ---------------------------------------------------------------------------


class TestGetTemplatesFilename:
    def test_no_config_uses_fallback(self):
        strategy = DefaultSchedulerStrategy()
        result = strategy.get_templates_filename("my-provider", "aws")
        assert result == "aws_templates.json"

    def test_config_filename_patterns_override(self):
        strategy = DefaultSchedulerStrategy()
        config = {
            "template": {"filename_patterns": {"provider_type": "{provider_type}_custom.json"}}
        }
        result = strategy.get_templates_filename("my-provider", "aws", config=config)
        assert result == "aws_custom.json"

    def test_config_templates_filename_override(self):
        strategy = DefaultSchedulerStrategy()
        config = {"template": {"templates_filename": "fixed_name.json"}}
        result = strategy.get_templates_filename("my-provider", "aws", config=config)
        assert result == "fixed_name.json"


# ---------------------------------------------------------------------------
# _delegate_load_to_strategy
# ---------------------------------------------------------------------------


class TestDelegateLoadToStrategy:
    # The import of get_scheduler_registry is done inside the method body with
    # a local import from orb.infrastructure.scheduler.registry, so we patch there.

    def test_returns_none_when_scheduler_type_not_registered(self):
        strategy = DefaultSchedulerStrategy()
        strategy._config_manager = None

        with patch("orb.infrastructure.scheduler.registry.get_scheduler_registry") as mock_reg_fn:
            mock_registry = MagicMock()
            mock_registry.is_registered.return_value = False
            mock_reg_fn.return_value = mock_registry

            result = strategy._delegate_load_to_strategy("unknown_type", "/some/path.json")

        assert result is None

    def test_raises_on_fail_action_when_type_not_registered(self):
        strategy = make_strategy_with_config(on_mismatch="fail")

        with patch("orb.infrastructure.scheduler.registry.get_scheduler_registry") as mock_reg_fn:
            mock_registry = MagicMock()
            mock_registry.is_registered.return_value = False
            mock_reg_fn.return_value = mock_registry

            from orb.infrastructure.template.configuration_manager import (
                TemplateConfigurationError,
            )

            with pytest.raises(TemplateConfigurationError):
                strategy._delegate_load_to_strategy("unknown_type", "/some/path.json")

    def test_returns_none_when_type_not_registered_and_ignore_action(self):
        strategy = make_strategy_with_config(on_mismatch="ignore")

        with patch("orb.infrastructure.scheduler.registry.get_scheduler_registry") as mock_reg_fn:
            mock_registry = MagicMock()
            mock_registry.is_registered.return_value = False
            mock_reg_fn.return_value = mock_registry

            result = strategy._delegate_load_to_strategy("unknown_type", "/some/path.json")

        assert result is None

    def test_delegates_loading_when_type_registered(self):
        strategy = DefaultSchedulerStrategy()

        mock_delegate_strategy = MagicMock()
        mock_delegate_strategy.load_templates_from_path.return_value = [{"template_id": "t1"}]

        mock_strategy_class = MagicMock(return_value=mock_delegate_strategy)

        with patch("orb.infrastructure.scheduler.registry.get_scheduler_registry") as mock_reg_fn:
            mock_registry = MagicMock()
            mock_registry.is_registered.return_value = True
            mock_registry.get_strategy_class.return_value = mock_strategy_class
            mock_reg_fn.return_value = mock_registry

            result = strategy._delegate_load_to_strategy("hostfactory", "/some/path.json")

        assert result == [{"template_id": "t1"}]

    def test_returns_none_on_strategy_construction_error(self):
        strategy = DefaultSchedulerStrategy()

        mock_strategy_class = MagicMock(side_effect=Exception("construction failed"))

        with patch("orb.infrastructure.scheduler.registry.get_scheduler_registry") as mock_reg_fn:
            mock_registry = MagicMock()
            mock_registry.is_registered.return_value = True
            mock_registry.get_strategy_class.return_value = mock_strategy_class
            mock_reg_fn.return_value = mock_registry

            result = strategy._delegate_load_to_strategy("hostfactory", "/some/path.json")

        assert result is None


# ---------------------------------------------------------------------------
# get_template_paths
# ---------------------------------------------------------------------------


class TestGetTemplatePaths:
    def test_returns_generic_path_at_minimum(self):
        strategy = make_strategy_with_config()
        strategy._config_manager.resolve_file.return_value = "/config/templates.json"  # type: ignore[misc,attr-defined,union-attr]
        strategy._config_manager.get_provider_config.side_effect = Exception("no providers")  # type: ignore[misc,attr-defined,union-attr]

        paths = strategy.get_template_paths()
        assert len(paths) >= 1
        assert any("templates.json" in p for p in paths)

    def test_deduplicates_paths(self):
        strategy = make_strategy_with_config()

        # Both provider-specific and generic resolve to same path
        strategy._config_manager.resolve_file.return_value = "/config/templates.json"  # type: ignore[misc,attr-defined,union-attr]

        mock_provider = MagicMock()
        mock_provider.type = "aws"
        mock_provider_config = MagicMock()
        mock_provider_config.get_active_providers.return_value = [mock_provider]
        strategy._config_manager.get_provider_config.return_value = mock_provider_config  # type: ignore[misc,attr-defined,union-attr]

        with (
            patch.object(strategy, "_get_provider_name", return_value="my-provider"),
            patch.object(strategy, "_get_active_provider_type", return_value="aws"),
        ):
            paths = strategy.get_template_paths()

        # No duplicates
        assert len(paths) == len(set(paths))
