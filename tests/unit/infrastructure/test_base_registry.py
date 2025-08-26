"""Tests for base registry and scheduler registry."""

from unittest.mock import Mock

import pytest

from domain.base.exceptions import ConfigurationError
from infrastructure.registry.base_registry import BaseRegistration, BaseRegistry
from infrastructure.registry.scheduler_registry import (
    SchedulerRegistry,
    UnsupportedSchedulerError,
    get_scheduler_registry,
)


class TestBaseRegistry:
    """Test base registry functionality."""

    def test_base_registration_creation(self):
        """Test base registration creation."""
        strategy_factory = Mock()
        config_factory = Mock()

        registration = BaseRegistration("test_type", strategy_factory, config_factory)

        assert registration.type_name == "test_type"
        assert registration.strategy_factory == strategy_factory
        assert registration.config_factory == config_factory


class TestSchedulerRegistry:
    """Test scheduler registry functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.registry = SchedulerRegistry()
        # Clear any existing registrations
        with self.registry._registry_lock:
            self.registry._registrations.clear()

    def test_scheduler_registry_singleton(self):
        """Test scheduler registry singleton behavior."""
        registry1 = get_scheduler_registry()
        registry2 = get_scheduler_registry()

        assert registry1 is registry2
        assert isinstance(registry1, SchedulerRegistry)
        assert isinstance(registry1, BaseRegistry)

    def test_register_scheduler_strategy(self):
        """Test registering a scheduler strategy."""
        strategy_factory = Mock()
        config_factory = Mock()

        self.registry.register("hostfactory", strategy_factory, config_factory)

        assert self.registry.is_registered("hostfactory")
        assert "hostfactory" in self.registry.get_registered_types()

    def test_register_duplicate_scheduler_raises_error(self):
        """Test that registering duplicate scheduler raises error."""
        strategy_factory = Mock()
        config_factory = Mock()

        self.registry.register("hostfactory", strategy_factory, config_factory)

        with pytest.raises(ConfigurationError, match="already registered"):
            self.registry.register("hostfactory", strategy_factory, config_factory)

    def test_create_scheduler_strategy(self):
        """Test creating a scheduler strategy."""
        mock_strategy = Mock()
        strategy_factory = Mock(return_value=mock_strategy)
        config_factory = Mock()
        config = {"test": "config"}

        self.registry.register("hostfactory", strategy_factory, config_factory)

        result = self.registry.create_strategy("hostfactory", config)

        assert result == mock_strategy
        strategy_factory.assert_called_once_with(config)

    def test_create_strategy_for_unregistered_type_raises_error(self):
        """Test that creating strategy for unregistered type raises error."""
        with pytest.raises(UnsupportedSchedulerError, match="not registered"):
            self.registry.create_strategy("unknown", {})

    def test_create_strategy_factory_error_raises_configuration_error(self):
        """Test that factory errors are wrapped in ConfigurationError."""
        strategy_factory = Mock(side_effect=Exception("Factory error"))
        config_factory = Mock()

        self.registry.register("hostfactory", strategy_factory, config_factory)

        with pytest.raises(ConfigurationError, match="Failed to create scheduler strategy"):
            self.registry.create_strategy("hostfactory", {})

    def test_is_registered(self):
        """Test is_registered method."""
        strategy_factory = Mock()
        config_factory = Mock()

        assert not self.registry.is_registered("hostfactory")

        self.registry.register("hostfactory", strategy_factory, config_factory)

        assert self.registry.is_registered("hostfactory")
        assert not self.registry.is_registered("unknown")

    def test_get_registered_types(self):
        """Test get_registered_types method."""
        strategy_factory = Mock()
        config_factory = Mock()

        assert self.registry.get_registered_types() == []

        self.registry.register("hostfactory", strategy_factory, config_factory)
        self.registry.register("hf", strategy_factory, config_factory)

        registered_types = self.registry.get_registered_types()
        assert "hostfactory" in registered_types
        assert "hf" in registered_types
        assert len(registered_types) == 2
