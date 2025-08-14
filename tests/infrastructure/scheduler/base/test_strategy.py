"""Tests for base scheduler strategy."""

from unittest.mock import Mock

import pytest

from src.infrastructure.scheduler.base.strategy import BaseSchedulerStrategy


class ConcreteSchedulerStrategy(BaseSchedulerStrategy):
    """Concrete implementation for testing."""

    def get_templates(self):
        return [{"template_id": "test", "name": "Test Template"}]

    def format_output(self, data, output_format="json"):
        return f"formatted_{output_format}_{data}"


class TestBaseSchedulerStrategy:
    """Test cases for BaseSchedulerStrategy."""

    def test_initialization(self):
        """Test base scheduler strategy initialization."""
        config_manager = Mock()
        logger = Mock()

        strategy = ConcreteSchedulerStrategy(config_manager, logger)

        assert strategy.config_manager is config_manager
        assert strategy.logger is logger

    def test_abstract_methods_implemented(self):
        """Test that concrete implementation provides required methods."""
        config_manager = Mock()
        logger = Mock()

        strategy = ConcreteSchedulerStrategy(config_manager, logger)

        templates = strategy.get_templates()
        assert isinstance(templates, list)
        assert len(templates) == 1
        assert templates[0]["template_id"] == "test"

        output = strategy.format_output("test_data", "json")
        assert output == "formatted_json_test_data"

    def test_cannot_instantiate_abstract_base(self):
        """Test that BaseSchedulerStrategy cannot be instantiated directly."""
        config_manager = Mock()
        logger = Mock()

        with pytest.raises(TypeError):
            BaseSchedulerStrategy(config_manager, logger)
