"""Tests for base scheduler strategy."""

from typing import Any
from unittest.mock import Mock

import pytest

from domain.machine.aggregate import Machine
from domain.request.aggregate import Request
from domain.template.template_aggregate import Template
from infrastructure.scheduler.base.strategy import BaseSchedulerStrategy


class ConcreteSchedulerStrategy(BaseSchedulerStrategy):
    """Concrete implementation for testing."""

    def get_templates_file_path(self) -> str:
        return "/test/templates.json"

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
        return {"request": request_data}

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

    def format_request_response(self, request_data: dict[str, Any]) -> dict[str, Any]:
        return {"response": True}

    def format_machine_status_response(self, machines: list[Machine]) -> dict[str, Any]:
        return {"machines": []}

    def get_working_directory(self) -> str:
        return "/test/workdir"

    def get_config_directory(self) -> str:
        return "/test/confdir"

    def get_logs_directory(self) -> str:
        return "/test/logsdir"

    def get_directory(self, file_type: str) -> str | None:
        return f"/test/{file_type}dir"

    def get_storage_base_path(self) -> str:
        return "/test/storage"


class TestBaseSchedulerStrategy:
    """Test cases for BaseSchedulerStrategy."""

    def test_initialization(self):
        """Test base scheduler strategy initialization."""
        config_manager = Mock()
        logger = Mock()

        strategy = ConcreteSchedulerStrategy(config_manager, logger)

        assert strategy.config_manager is config_manager
        assert strategy.logger is logger

    def test_scheduler_port_methods_implemented(self):
        """Test that concrete implementation provides required SchedulerPort methods."""
        config_manager = Mock()
        logger = Mock()

        strategy = ConcreteSchedulerStrategy(config_manager, logger)

        # Test all SchedulerPort methods are implemented
        assert strategy.get_templates_file_path() == "/test/templates.json"
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
