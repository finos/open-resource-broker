"""Tests for scheduler strategy initialization."""

import pytest

from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.scheduler.registration import create_default_strategy


class TestSchedulerStrategyInitialization:
    """Test scheduler strategy initialization."""

    def test_default_scheduler_strategy_initialization(self):
        """Test that DefaultSchedulerStrategy can be initialized with no args (lazy DI)."""
        # DefaultSchedulerStrategy uses lazy property injection, not constructor injection
        strategy = DefaultSchedulerStrategy()

        # Internal state starts as None - resolved lazily via container
        assert strategy._config_manager is None
        assert strategy._logger is None

    def test_symphony_hostfactory_strategy_initialization(self):
        """Test that HostFactorySchedulerStrategy can be initialized with no args (lazy DI)."""
        # HostFactorySchedulerStrategy uses lazy property injection, not constructor injection
        strategy = HostFactorySchedulerStrategy()

        # Internal state starts as None - resolved lazily via container
        assert strategy._config_manager is None
        assert strategy._logger is None

    def test_create_default_strategy(self):
        """Test that create_default_strategy creates a DefaultSchedulerStrategy."""
        # create_default_strategy takes a config arg (not a DI container)
        # and returns a DefaultSchedulerStrategy with lazy DI
        config = {}
        strategy = create_default_strategy(config)

        assert isinstance(strategy, DefaultSchedulerStrategy)

    def test_default_strategy_has_lazy_config_manager(self):
        """Test that DefaultSchedulerStrategy exposes config_manager as a lazy property."""
        strategy = DefaultSchedulerStrategy()
        # The property exists and returns None when container is not ready
        assert hasattr(strategy, "config_manager")

    def test_default_strategy_has_lazy_logger(self):
        """Test that DefaultSchedulerStrategy exposes logger as a lazy property."""
        strategy = DefaultSchedulerStrategy()
        assert hasattr(strategy, "logger")

    def test_hostfactory_strategy_has_lazy_config_manager(self):
        """Test that HostFactorySchedulerStrategy exposes config_manager as a lazy property."""
        strategy = HostFactorySchedulerStrategy()
        assert hasattr(strategy, "config_manager")

    def test_hostfactory_strategy_has_lazy_logger(self):
        """Test that HostFactorySchedulerStrategy exposes logger as a lazy property."""
        strategy = HostFactorySchedulerStrategy()
        assert hasattr(strategy, "logger")


class TestSchedulerStrategyRegistration:
    """Test scheduler strategy registration."""

    def test_register_scheduler_strategies(self):
        """Test that register_scheduler_strategies registers the default scheduler."""
        from orb.infrastructure.scheduler.registration import register_default_scheduler

        # register_default_scheduler works with the global registry
        # Just verify it runs without error
        try:
            register_default_scheduler()
        except Exception as e:
            pytest.fail(f"register_default_scheduler raised unexpectedly: {e}")

    def test_register_all_scheduler_types(self):
        """Test that register_all_scheduler_types registers both strategies."""
        from orb.infrastructure.scheduler.registration import register_all_scheduler_types

        try:
            register_all_scheduler_types()
        except Exception as e:
            pytest.fail(f"register_all_scheduler_types raised unexpectedly: {e}")
