"""Tests for base scheduler strategy."""

from typing import Any, Dict, List
from unittest.mock import Mock

import pytest

from src.domain.machine.aggregate import Machine
from src.domain.request.aggregate import Request
from src.domain.template.aggregate import Template
from src.infrastructure.scheduler.base.strategy import BaseSchedulerStrategy


class ConcreteSchedulerStrategy(BaseSchedulerStrategy):
    """Concrete implementation for testing."""

    def get_templates_file_path(self) -> str:
        return "/test/templates.json"

    def get_config_file_path(self) -> str:
        return "/test/config.json"

    def parse_template_config(self, raw_data: Dict[str, Any]) -> Template:
        return Mock(spec=Template)

    def parse_request_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"parsed": True}

    def format_templates_response(self, templates: List[Template]) -> Dict[str, Any]:
        return {"templates": []}

    def format_request_status_response(self, requests: List[Request]) -> Dict[str, Any]:
        return {"requests": []}

    def format_request_response(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"response": True}

    def format_machine_status_response(self, machines: List[Machine]) -> Dict[str, Any]:
        return {"machines": []}

    def get_working_directory(self) -> str:
        return "/test/workdir"

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
