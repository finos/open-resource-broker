"""Tests for scheduler strategy initialization."""

from unittest.mock import MagicMock, patch

from src.config.manager import ConfigurationManager
from src.domain.base.ports import LoggingPort, SchedulerPort
from src.infrastructure.scheduler.default.strategy import DefaultSchedulerStrategy
from src.infrastructure.scheduler.hostfactory.strategy import (
    HostFactorySchedulerStrategy,
)
from src.infrastructure.scheduler.registration import create_default_strategy


class TestSchedulerStrategyInitialization:
    """Test scheduler strategy initialization."""

    def test_default_scheduler_strategy_initialization(self):
        """Test that DefaultSchedulerStrategy can be initialized with config_manager and logger."""
        # Arrange
        config_manager = MagicMock(spec=ConfigurationManager)
        logger = MagicMock(spec=LoggingPort)

        # Act
        strategy = DefaultSchedulerStrategy(config_manager, logger)

        # Assert
        assert strategy.config_manager == config_manager
        assert strategy._logger == logger

    def test_symphony_hostfactory_strategy_initialization(self):
        """Test that HostFactorySchedulerStrategy can be initialized with config_manager and logger."""
        # Arrange
        config_manager = MagicMock(spec=ConfigurationManager)
        logger = MagicMock(spec=LoggingPort)

        # Act
        strategy = HostFactorySchedulerStrategy(config_manager, logger)

        # Assert
        assert strategy.config_manager == config_manager
        assert strategy._logger == logger

    def test_create_default_strategy(self):
        """Test that create_default_strategy creates a DefaultSchedulerStrategy with config_manager and logger."""
        # Arrange
        container = MagicMock()
        config_manager = MagicMock(spec=ConfigurationManager)
        logger = MagicMock(spec=LoggingPort)
        container.get.side_effect = lambda x: {
            ConfigurationManager: config_manager,
            LoggingPort: logger,
        }.get(x)

        # Act
        strategy = create_default_strategy(container)

        # Assert
        assert isinstance(strategy, DefaultSchedulerStrategy)
        assert strategy.config_manager == config_manager
        assert strategy._logger == logger
        container.get.assert_any_call(ConfigurationManager)
        container.get.assert_any_call(LoggingPort)


class TestSchedulerStrategyRegistration:
    """Test scheduler strategy registration."""

    @patch("src.infrastructure.scheduler.registration.create_default_strategy")
    def test_register_scheduler_strategies(self, mock_create_default_strategy):
        """Test that register_scheduler_strategies registers the default strategy."""
        # Arrange
        from src.infrastructure.scheduler.registration import (
            register_scheduler_strategies,
        )

        container = MagicMock()
        mock_strategy = MagicMock(spec=SchedulerPort)
        mock_create_default_strategy.return_value = mock_strategy

        # Act
        register_scheduler_strategies(container)

        # Assert
        mock_create_default_strategy.assert_called_once_with(container)
        container.register_singleton.assert_called_once_with(SchedulerPort, lambda c: mock_strategy)
