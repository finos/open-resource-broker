"""Tests for base scheduler strategy."""

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from orb.domain.machine.aggregate import Machine
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.scheduler.base.strategy import BaseSchedulerStrategy


class ConcreteSchedulerStrategy(BaseSchedulerStrategy):
    """Concrete implementation for testing."""

    def __init__(self, config_manager_instance=None, logger_instance=None):
        self._config_manager_instance = config_manager_instance
        self._logger_instance = logger_instance

    @property
    def config_manager(self) -> Any:
        return self._config_manager_instance

    def get_config_file_path(self) -> str:
        return "/test/config.json"

    def parse_template_config(self, raw_data: dict[str, Any]) -> Template:
        return Mock(spec=Template)

    def parse_request_data(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        return {"parsed": True}

    def format_templates_response(self, templates: list[Template]) -> dict[str, Any]:
        return {"templates": []}

    def format_templates_for_generation(self, templates: list[dict]) -> list[dict]:
        return templates

    def format_request_response(self, request_data: dict[str, Any]) -> dict[str, Any]:
        return {"response": True}

    def format_machine_status_response(self, machines: list[Machine]) -> dict[str, Any]:
        return {"machines": []}

    def get_storage_base_path(self) -> str:
        return "/test/storage"

    def format_template_for_provider(self, template: Template) -> dict[str, Any]:
        return {"template": "provider"}

    def format_request_for_display(self, request: Request) -> dict[str, Any]:
        return {"request": "display"}

    def get_exit_code_for_status(self, status: str) -> int:
        return 0

    def get_directory(self, file_type: str) -> str | None:
        return f"/test/{file_type}dir"

    def get_templates_filename(self, provider_name: str, provider_type: str) -> str:
        return f"{provider_name}_{provider_type}_templates.json"

    def should_log_to_console(self) -> bool:
        return True

    def format_error_response(self, error: Exception, context: dict[str, Any]) -> dict[str, Any]:
        return {"error": str(error)}

    def format_health_response(self, checks: list[dict[str, Any]]) -> dict[str, Any]:
        return {"health": "ok"}

    def format_request_status_response(self, requests: list[Request]) -> dict[str, Any]:
        return {"requests": []}

    def get_working_directory(self) -> str:
        return "/test/workdir"

    def format_machine_details_response(self, machine_data: dict) -> dict:
        return {"machine": machine_data}

    def get_config_directory(self) -> str:
        return "/test/confdir"

    def get_logs_directory(self) -> str:
        return "/test/logsdir"

    def get_scripts_directory(self) -> Path | None:
        return None

    def get_scheduler_type(self) -> str:
        return "test"

    def _get_provider_name(self) -> str:
        return "test_provider"

    def _get_active_provider_type(self) -> str:
        return "aws"


class TestBaseSchedulerStrategy:
    """Test cases for BaseSchedulerStrategy."""

    def test_initialization(self):
        """Test base scheduler strategy initialization."""
        config_manager = Mock()
        logger = Mock()

        strategy = ConcreteSchedulerStrategy(config_manager, logger)

        assert strategy.config_manager is config_manager

    def test_scheduler_port_methods_implemented(self):
        """Test that concrete implementation provides required SchedulerPort methods."""
        config_manager = Mock()
        logger = Mock()

        strategy = ConcreteSchedulerStrategy(config_manager, logger)

        # Test all SchedulerPort methods are implemented
        assert strategy.get_config_file_path() == "/test/config.json"
        assert strategy.parse_request_data({"test": "data"}) == {"parsed": True}
        assert strategy.format_templates_response([]) == {"templates": []}
        assert strategy.format_request_status_response([]) == {"requests": []}
        assert strategy.format_request_response({}) == {"response": True}
        assert strategy.format_machine_status_response([]) == {"machines": []}
        assert strategy.get_working_directory() == "/test/workdir"
        assert strategy.get_storage_base_path() == "/test/storage"

    def test_cannot_instantiate_abstract_base(self):
        """Test that BaseSchedulerStrategy cannot be instantiated directly."""
        config_manager = Mock()
        logger = Mock()

        with pytest.raises(TypeError):
            BaseSchedulerStrategy(config_manager, logger)

    def test_get_log_level_reads_from_injected_config(self):
        """get_log_level returns level from injected config when no scheduler override."""
        config_manager = Mock()
        config_manager.app_config.scheduler.log_level = None
        config_manager.get_logging_config.return_value = {"level": "DEBUG"}

        strategy = ConcreteSchedulerStrategy(config_manager)
        strategy._init_base(config_port=config_manager)

        assert strategy.get_log_level() == "DEBUG"

    def test_get_log_level_does_not_read_env_directly(self, monkeypatch):
        """get_log_level does not fall back to os.environ for ORB_LOG_LEVEL."""
        monkeypatch.setenv("ORB_LOG_LEVEL", "WARNING")

        config_manager = Mock()
        config_manager.app_config.scheduler.log_level = None
        config_manager.get_logging_config.return_value = {}

        strategy = ConcreteSchedulerStrategy(config_manager)
        strategy._init_base(config_port=config_manager)

        # No env fallback — returns hard default
        assert strategy.get_log_level() == "INFO"

    def test_coalesce_directory_uses_default_factory_when_no_override(self):
        """_coalesce_directory calls default_factory when config and env are absent."""
        strategy = ConcreteSchedulerStrategy()
        result = strategy._coalesce_directory(
            config_override=None,
            env_var_name="NONEXISTENT_VAR",
            default_factory=lambda: "/default/path",
        )
        assert result == "/default/path"
