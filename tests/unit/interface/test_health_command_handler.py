"""Unit tests for health_command_handler — all branch and error paths."""

from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.application.ports.scheduler_port import SchedulerPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.console_port import ConsolePort
from orb.domain.base.ports.health_check_port import HealthCheckPort

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _make_container(overrides: dict | None = None):
    """Build a DI container mock with sensible defaults for health checks."""
    from orb.application.services.provider_registry_service import ProviderRegistryService
    from orb.domain.template.repository import TemplateRepository

    container = MagicMock()

    # scheduler_strategy: default behaviour — log-to-console, format_health_response
    mock_scheduler = MagicMock(spec=SchedulerPort)
    mock_scheduler.get_template_paths.return_value = []
    mock_scheduler.get_working_directory.return_value = "/tmp/work"
    mock_scheduler.get_logs_directory.return_value = "/tmp/logs"
    mock_scheduler.should_log_to_console.return_value = True
    mock_scheduler.format_health_response.return_value = {
        "checks": [],
        "summary": {"passed": 0, "total": 0, "status": "ok"},
    }

    mock_config = MagicMock(spec=ConfigurationPort)
    mock_config.get_config_file_path.return_value = None

    mock_health_port = MagicMock(spec=HealthCheckPort)
    mock_health_port.run_all_checks.return_value = {}

    mock_console = MagicMock(spec=ConsolePort)

    mock_template_repo = MagicMock(spec=TemplateRepository)
    mock_template_repo.find_active_templates.return_value = []

    mock_registry_service = MagicMock(spec=ProviderRegistryService)
    mock_registry_service.get_available_strategies.return_value = []

    dispatch: dict[Any, Any] = {
        SchedulerPort: mock_scheduler,
        ConfigurationPort: mock_config,
        HealthCheckPort: mock_health_port,
        ConsolePort: mock_console,
        TemplateRepository: mock_template_repo,
        ProviderRegistryService: mock_registry_service,
    }
    if overrides:
        dispatch.update(overrides)

    container.get.side_effect = lambda t: dispatch.get(t, MagicMock())
    return container, {
        "scheduler": mock_scheduler,
        "config": mock_config,
        "health_port": mock_health_port,
        "console": mock_console,
        "template_repo": mock_template_repo,
        "registry_service": mock_registry_service,
    }


# ---------------------------------------------------------------------------
# Basic happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleHealthCheckHappyPath:
    def test_returns_dict_with_summary(self, tmp_path):
        """handle_health_check returns the formatted response dict."""
        from orb.interface.health_command_handler import handle_health_check

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")

        container, mocks = _make_container()
        mocks["config"].get_config_file_path.return_value = str(cfg_file)
        mocks["scheduler"].get_working_directory.return_value = str(tmp_path)
        mocks["scheduler"].get_logs_directory.return_value = str(tmp_path)
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 1, "total": 1, "status": "ok"},
        }

        args = _make_args()
        args._container = container

        result = handle_health_check(args)

        assert isinstance(result, dict)
        assert "summary" in result

    def test_format_health_response_called_with_checks_list(self, tmp_path):
        """scheduler.format_health_response receives a list of check dicts."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 0, "status": "ok"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        mocks["scheduler"].format_health_response.assert_called_once()
        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        assert isinstance(checks_arg, list)


# ---------------------------------------------------------------------------
# HealthCheckPort branch paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthCheckPortBranches:
    def test_healthy_status_maps_to_pass(self):
        """run_all_checks returning 'healthy' → 'pass' CLI status."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["health_port"].run_all_checks.return_value = {
            "database": {"status": "healthy", "details": {"latency": "5ms"}}
        }
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [{"name": "database", "status": "pass"}],
            "summary": {"passed": 1, "total": 1, "status": "ok"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        db_check = next(c for c in checks_arg if c["name"] == "database")
        assert db_check["status"] == "pass"

    def test_unknown_status_maps_to_warn(self):
        """run_all_checks returning 'unknown' → 'warn' CLI status."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["health_port"].run_all_checks.return_value = {
            "cache": {"status": "unknown", "details": {}}
        }
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "warn"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        cache_check = next(c for c in checks_arg if c["name"] == "cache")
        assert cache_check["status"] == "warn"

    def test_non_healthy_non_unknown_maps_to_fail(self):
        """run_all_checks returning 'degraded' → 'fail' CLI status."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["health_port"].run_all_checks.return_value = {
            "scheduler": {"status": "degraded", "details": {"error": "timeout"}}
        }
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "fail"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        sched_check = next(c for c in checks_arg if c["name"] == "scheduler")
        assert sched_check["status"] == "fail"

    def test_health_port_exception_appended_as_warn(self):
        """If run_all_checks raises, a 'warn' check is appended for health_monitor."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["health_port"].run_all_checks.side_effect = RuntimeError("db down")
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "warn"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        names = [c["name"] for c in checks_arg]
        assert "health_monitor" in names
        hm = next(c for c in checks_arg if c["name"] == "health_monitor")
        assert hm["status"] == "warn"


# ---------------------------------------------------------------------------
# Config file check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigFileCheck:
    def test_config_file_missing_gives_fail_status(self, tmp_path):
        """Config file path that does not exist → 'fail' check status."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["config"].get_config_file_path.return_value = str(tmp_path / "nonexistent.json")
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "fail"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        cfg_check = next(c for c in checks_arg if c["name"] == "config_file")
        assert cfg_check["status"] == "fail"

    def test_config_file_exists_gives_pass_status(self, tmp_path):
        """Config file path that exists → 'pass' check status."""
        from orb.interface.health_command_handler import handle_health_check

        cfg = tmp_path / "config.json"
        cfg.write_text("{}")

        container, mocks = _make_container()
        mocks["config"].get_config_file_path.return_value = str(cfg)
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 1, "total": 1, "status": "ok"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        cfg_check = next(c for c in checks_arg if c["name"] == "config_file")
        assert cfg_check["status"] == "pass"

    def test_none_config_path_uses_default(self):
        """get_config_file_path returns None → default path './config/config.json' is used."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["config"].get_config_file_path.return_value = None
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "fail"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        cfg_check = next(c for c in checks_arg if c["name"] == "config_file")
        assert "config.json" in cfg_check["details"]


# ---------------------------------------------------------------------------
# Template paths / templates loaded checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateChecks:
    def test_existing_template_paths_gives_pass(self, tmp_path):
        """Scheduler returns a path that exists → templates_file 'pass'."""
        from orb.interface.health_command_handler import handle_health_check

        tpl = tmp_path / "templates.json"
        tpl.write_text("{}")

        container, mocks = _make_container()
        mocks["scheduler"].get_template_paths.return_value = [str(tpl)]
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 1, "total": 1, "status": "ok"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        tpl_check = next(c for c in checks_arg if c["name"] == "templates_file")
        assert tpl_check["status"] == "pass"

    def test_no_existing_template_paths_gives_fail(self, tmp_path):
        """Scheduler returns a path that does not exist → templates_file 'fail'."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["scheduler"].get_template_paths.return_value = [str(tmp_path / "ghost.json")]
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "fail"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        tpl_check = next(c for c in checks_arg if c["name"] == "templates_file")
        assert tpl_check["status"] == "fail"

    def test_template_paths_exception_gives_fail(self):
        """get_template_paths raising → templates_file 'fail' with error appended."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["scheduler"].get_template_paths.side_effect = RuntimeError("no config")
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "fail"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        tpl_check = next(c for c in checks_arg if c["name"] == "templates_file")
        assert tpl_check["status"] == "fail"
        assert "error" in tpl_check

    def test_templates_loaded_empty_gives_warn(self):
        """find_active_templates returns [] → 'warn' status (not 'fail')."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["template_repo"].find_active_templates.return_value = []
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "warn"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        loaded_check = next(c for c in checks_arg if c["name"] == "templates_loaded")
        assert loaded_check["status"] == "warn"

    def test_templates_loaded_non_empty_gives_pass(self):
        """find_active_templates returns non-empty list → 'pass' status."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["template_repo"].find_active_templates.return_value = [{"template_id": "t1"}]
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 1, "total": 1, "status": "ok"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        loaded_check = next(c for c in checks_arg if c["name"] == "templates_loaded")
        assert loaded_check["status"] == "pass"

    def test_templates_loaded_exception_gives_fail(self):
        """find_active_templates raising → 'fail' status with error."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["template_repo"].find_active_templates.side_effect = Exception("db error")
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "fail"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        loaded_check = next(c for c in checks_arg if c["name"] == "templates_loaded")
        assert loaded_check["status"] == "fail"


# ---------------------------------------------------------------------------
# Provider health check paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderHealthCheck:
    def test_no_providers_gives_warn(self):
        """get_available_strategies returns [] → 'warn' provider_health check."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["registry_service"].get_available_strategies.return_value = []
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "warn"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        ph = next(c for c in checks_arg if c["name"] == "provider_health")
        assert ph["status"] == "warn"

    def test_all_healthy_providers_gives_pass(self):
        """All providers return is_healthy=True → 'pass' provider_health check."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["registry_service"].get_available_strategies.return_value = ["aws", "k8s"]

        healthy_status = MagicMock()
        healthy_status.is_healthy = True
        mocks["registry_service"].check_strategy_health.return_value = healthy_status
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 1, "total": 1, "status": "ok"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        ph = next(c for c in checks_arg if c["name"] == "provider_health")
        assert ph["status"] == "pass"

    def test_partial_healthy_providers_gives_warn(self):
        """Some providers healthy, some not → 'warn' provider_health check."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["registry_service"].get_available_strategies.return_value = ["aws", "k8s"]

        healthy = MagicMock()
        healthy.is_healthy = True
        unhealthy = MagicMock()
        unhealthy.is_healthy = False
        unhealthy.status_message = "connection refused"

        mocks["registry_service"].check_strategy_health.side_effect = [healthy, unhealthy]
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "warn"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        ph = next(c for c in checks_arg if c["name"] == "provider_health")
        assert ph["status"] == "warn"

    def test_no_providers_healthy_gives_warn(self):
        """No providers healthy → 'warn' (not fail) provider_health check."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["registry_service"].get_available_strategies.return_value = ["aws"]

        unhealthy = MagicMock()
        unhealthy.is_healthy = False
        unhealthy.status_message = "timed out"
        mocks["registry_service"].check_strategy_health.return_value = unhealthy
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "warn"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        ph = next(c for c in checks_arg if c["name"] == "provider_health")
        assert ph["status"] == "warn"

    def test_health_check_returns_none_appends_error_message(self):
        """check_strategy_health returning None → error string appended."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["registry_service"].get_available_strategies.return_value = ["aws"]
        mocks["registry_service"].check_strategy_health.return_value = None
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "warn"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        ph = next(c for c in checks_arg if c["name"] == "provider_health")
        assert ph["status"] == "warn"

    def test_per_provider_exception_appended_as_error(self):
        """check_strategy_health raising → error string added for that provider."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["registry_service"].get_available_strategies.return_value = ["aws"]
        mocks["registry_service"].check_strategy_health.side_effect = RuntimeError(
            "provider crashed"
        )
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "warn"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        ph = next(c for c in checks_arg if c["name"] == "provider_health")
        assert ph["status"] == "warn"
        assert "provider crashed" in ph["details"]

    def test_provider_registry_exception_appended_as_warn(self):
        """If getting registry service itself raises → 'warn' appended."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["registry_service"].get_available_strategies.side_effect = Exception(
            "registry unavailable"
        )
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "warn"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        ph = next(c for c in checks_arg if c["name"] == "provider_health")
        assert ph["status"] == "warn"


# ---------------------------------------------------------------------------
# Work dir / logs dir checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWorkAndLogsDirectoryChecks:
    def test_work_directory_exists_gives_pass(self, tmp_path):
        """Scheduler returns an existing work directory → 'pass'."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["scheduler"].get_working_directory.return_value = str(tmp_path)
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 1, "total": 1, "status": "ok"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        wd = next(c for c in checks_arg if c["name"] == "work_directory")
        assert wd["status"] == "pass"

    def test_work_directory_missing_gives_fail(self, tmp_path):
        """Scheduler returns a non-existent work directory → 'fail'."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["scheduler"].get_working_directory.return_value = str(tmp_path / "ghost")
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "fail"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        wd = next(c for c in checks_arg if c["name"] == "work_directory")
        assert wd["status"] == "fail"

    def test_work_directory_exception_gives_fail(self):
        """get_working_directory raising → 'fail' check."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["scheduler"].get_working_directory.side_effect = RuntimeError("not configured")
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "fail"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        wd = next(c for c in checks_arg if c["name"] == "work_directory")
        assert wd["status"] == "fail"

    def test_logs_directory_exists_gives_pass(self, tmp_path):
        """Scheduler returns an existing logs directory → 'pass'."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["scheduler"].get_logs_directory.return_value = str(tmp_path)
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 1, "total": 1, "status": "ok"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        ld = next(c for c in checks_arg if c["name"] == "logs_directory")
        assert ld["status"] == "pass"

    def test_logs_directory_missing_gives_fail(self, tmp_path):
        """Scheduler returns a non-existent logs directory → 'fail'."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["scheduler"].get_logs_directory.return_value = str(tmp_path / "ghost_logs")
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "fail"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        ld = next(c for c in checks_arg if c["name"] == "logs_directory")
        assert ld["status"] == "fail"

    def test_logs_directory_exception_gives_fail(self):
        """get_logs_directory raising → 'fail' check."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["scheduler"].get_logs_directory.side_effect = RuntimeError("not set")
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 1, "status": "fail"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        checks_arg = mocks["scheduler"].format_health_response.call_args[0][0]
        ld = next(c for c in checks_arg if c["name"] == "logs_directory")
        assert ld["status"] == "fail"


# ---------------------------------------------------------------------------
# Console output paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthConsoleOutput:
    def test_should_log_to_console_true_outputs_info(self):
        """When should_log_to_console() is True, console.info is called."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["scheduler"].should_log_to_console.return_value = True
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [{"name": "config_file", "status": "pass"}],
            "summary": {"passed": 1, "total": 1, "status": "ok"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        mocks["console"].info.assert_called()

    def test_should_log_to_console_false_no_console_output(self):
        """When should_log_to_console() is False (HF mode), console output is skipped."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["scheduler"].should_log_to_console.return_value = False
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [],
            "summary": {"passed": 0, "total": 0, "status": "ok"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        # In HF mode the console.info/success/warning/error for individual checks
        # must NOT be called; only the top-level error path may call console.
        mocks["console"].info.assert_not_called()

    def test_pass_check_calls_console_success(self, tmp_path):
        """A passing check in console mode calls console.success.

        The console loop iterates the *real* ``checks`` list built inside the
        handler, not the mocked ``format_health_response`` return value. So the
        test must arrange for at least one genuine 'pass' check. Pointing the
        config-file path at an existing file (and the work/logs dirs at an
        existing directory) guarantees a passing check deterministically,
        regardless of the process working directory.
        """
        from orb.interface.health_command_handler import handle_health_check

        cfg = tmp_path / "config.json"
        cfg.write_text("{}")

        container, mocks = _make_container()
        mocks["scheduler"].should_log_to_console.return_value = True
        mocks["config"].get_config_file_path.return_value = str(cfg)
        mocks["scheduler"].get_working_directory.return_value = str(tmp_path)
        mocks["scheduler"].get_logs_directory.return_value = str(tmp_path)
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [{"name": "config_file", "status": "pass"}],
            "summary": {"passed": 1, "total": 1, "status": "ok"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        mocks["console"].success.assert_called()

    def test_warn_check_calls_console_warning(self):
        """A warn check in console mode calls console.warning."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["scheduler"].should_log_to_console.return_value = True
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [{"name": "templates_loaded", "status": "warn", "error": "no templates"}],
            "summary": {"passed": 0, "total": 1, "status": "warn"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        mocks["console"].warning.assert_called()

    def test_fail_check_calls_console_error(self):
        """A fail check in console mode calls console.error."""
        from orb.interface.health_command_handler import handle_health_check

        container, mocks = _make_container()
        mocks["scheduler"].should_log_to_console.return_value = True
        mocks["scheduler"].format_health_response.return_value = {
            "checks": [{"name": "config_file", "status": "fail", "error": "not found"}],
            "summary": {"passed": 0, "total": 1, "status": "fail"},
        }

        args = _make_args()
        args._container = container

        handle_health_check(args)

        mocks["console"].error.assert_called()


# ---------------------------------------------------------------------------
# Outer exception fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthCheckOuterException:
    def test_outer_exception_returns_error_dict(self):
        """If an outer exception escapes, handle_health_check returns error dict."""
        from orb.interface.health_command_handler import handle_health_check

        container = MagicMock()
        container.get.side_effect = RuntimeError("container blew up")
        # ConsolePort call inside except block:
        mock_console = MagicMock(spec=ConsolePort)
        container.get.side_effect = lambda t: (
            mock_console if t is ConsolePort else (_ for _ in ()).throw(RuntimeError("boom"))
        )

        args = _make_args()
        args._container = container

        result = handle_health_check(args)

        assert result["success"] is False
        assert "message" in result
