"""Tests for API handler initialization."""

from unittest.mock import MagicMock, patch

from api.handlers.get_available_templates_handler import (
    GetAvailableTemplatesRESTHandler,
)
from api.handlers.get_request_status_handler import GetRequestStatusRESTHandler
from api.handlers.get_return_requests_handler import GetReturnRequestsRESTHandler
from api.handlers.request_machines_handler import RequestMachinesRESTHandler
from api.handlers.request_return_machines_handler import (
    RequestReturnMachinesRESTHandler,
)
from domain.base.ports import ErrorHandlingPort, LoggingPort, SchedulerPort
from infrastructure.di.buses import CommandBus, QueryBus
from monitoring.metrics import MetricsCollector


class TestAPIHandlerInitialization:
    """Test API handler initialization."""

    def setup_method(self):
        """Set up test dependencies."""
        self.query_bus = MagicMock(spec=QueryBus)
        self.command_bus = MagicMock(spec=CommandBus)
        self.scheduler_strategy = MagicMock(spec=SchedulerPort)
        self.logger = MagicMock(spec=LoggingPort)
        self.error_handler = MagicMock(spec=ErrorHandlingPort)
        self.metrics = MagicMock(spec=MetricsCollector)

    def test_get_available_templates_handler_initialization(self):
        """Test that GetAvailableTemplatesRESTHandler can be initialized with all dependencies."""
        # Act
        handler = GetAvailableTemplatesRESTHandler(
            query_bus=self.query_bus,
            command_bus=self.command_bus,
            scheduler_strategy=self.scheduler_strategy,
            metrics=self.metrics,
        )

        # Assert
        assert handler._query_bus == self.query_bus
        assert handler._command_bus == self.command_bus
        assert handler._scheduler_strategy == self.scheduler_strategy
        assert handler._metrics == self.metrics

    def test_request_machines_handler_initialization(self):
        """Test that RequestMachinesRESTHandler can be initialized with all dependencies."""
        # Act
        handler = RequestMachinesRESTHandler(
            query_bus=self.query_bus,
            command_bus=self.command_bus,
            logger=self.logger,
            error_handler=self.error_handler,
            metrics=self.metrics,
        )

        # Assert
        assert handler._query_bus == self.query_bus
        assert handler._command_bus == self.command_bus
        assert handler.logger == self.logger
        assert handler.error_handler == self.error_handler
        assert handler._metrics == self.metrics

    def test_get_request_status_handler_initialization(self):
        """Test that GetRequestStatusRESTHandler can be initialized with all dependencies."""
        # Act
        handler = GetRequestStatusRESTHandler(
            query_bus=self.query_bus,
            command_bus=self.command_bus,
            scheduler_strategy=self.scheduler_strategy,
            logger=self.logger,
            error_handler=self.error_handler,
            metrics=self.metrics,
        )

        # Assert
        assert handler._query_bus == self.query_bus
        assert handler._command_bus == self.command_bus
        assert handler._scheduler_strategy == self.scheduler_strategy
        assert handler.logger == self.logger
        assert handler.error_handler == self.error_handler
        assert handler._metrics == self.metrics

    def test_get_return_requests_handler_initialization(self):
        """Test that GetReturnRequestsRESTHandler can be initialized with all dependencies."""
        # Act
        handler = GetReturnRequestsRESTHandler(
            query_bus=self.query_bus,
            command_bus=self.command_bus,
            scheduler_strategy=self.scheduler_strategy,
            logger=self.logger,
            error_handler=self.error_handler,
            metrics=self.metrics,
        )

        # Assert
        assert handler._query_bus == self.query_bus
        assert handler._command_bus == self.command_bus
        assert handler._scheduler_strategy == self.scheduler_strategy
        assert handler.logger == self.logger
        assert handler.error_handler == self.error_handler
        assert handler._metrics == self.metrics

    def test_request_return_machines_handler_initialization(self):
        """Test that RequestReturnMachinesRESTHandler can be initialized with all dependencies."""
        # Act
        handler = RequestReturnMachinesRESTHandler(
            query_bus=self.query_bus,
            command_bus=self.command_bus,
            scheduler_strategy=self.scheduler_strategy,
            logger=self.logger,
            error_handler=self.error_handler,
            metrics=self.metrics,
        )

        # Assert
        assert handler._query_bus == self.query_bus
        assert handler._command_bus == self.command_bus
        assert handler._scheduler_strategy == self.scheduler_strategy
        assert handler.logger == self.logger
        assert handler.error_handler == self.error_handler
        assert handler._metrics == self.metrics


class TestAPIHandlerRegistration:
    """Test API handler registration in server_services.py."""

    @patch("src.infrastructure.di.server_services._register_api_handlers")
    def test_register_server_services(self, mock_register_api_handlers):
        """Test that register_server_services calls _register_api_handlers when server is enabled."""
        # Arrange
        from config.schemas.server_schema import ServerConfig
        from infrastructure.di.server_services import register_server_services

        container = MagicMock()
        config_manager = MagicMock()
        server_config = MagicMock(spec=ServerConfig)
        server_config.enabled = True
        config_manager.get_typed.return_value = server_config
        container.get.return_value = config_manager

        # Act
        register_server_services(container)

        # Assert
        mock_register_api_handlers.assert_called_once_with(container)

    @patch("src.infrastructure.di.server_services._register_api_handlers")
    def test_register_server_services_disabled(self, mock_register_api_handlers):
        """Test that register_server_services doesn't call _register_api_handlers when server is disabled."""
        # Arrange
        from config.schemas.server_schema import ServerConfig
        from infrastructure.di.server_services import register_server_services

        container = MagicMock()
        config_manager = MagicMock()
        server_config = MagicMock(spec=ServerConfig)
        server_config.enabled = False
        config_manager.get_typed.return_value = server_config
        container.get.return_value = config_manager

        # Act
        register_server_services(container)

        # Assert
        mock_register_api_handlers.assert_not_called()
