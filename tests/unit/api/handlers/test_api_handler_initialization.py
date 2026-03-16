"""Tests for API handler initialization."""

from unittest.mock import MagicMock, patch

from orb.api.handlers.get_available_templates_handler import (
    GetAvailableTemplatesRESTHandler,
)
from orb.api.handlers.get_request_status_handler import GetRequestStatusRESTHandler
from orb.api.handlers.get_return_requests_handler import GetReturnRequestsRESTHandler
from orb.api.handlers.request_machines_handler import RequestMachinesRESTHandler
from orb.api.handlers.request_return_machines_handler import (
    RequestReturnMachinesRESTHandler,
)
from orb.application.ports import SchedulerPort
from orb.domain.base.ports import ErrorHandlingPort, LoggingPort
from orb.infrastructure.di.buses import CommandBus, QueryBus
from orb.monitoring.metrics import MetricsCollector


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
        handler = GetAvailableTemplatesRESTHandler(
            query_bus=self.query_bus,
            command_bus=self.command_bus,
            scheduler_strategy=self.scheduler_strategy,
            metrics=self.metrics,
        )

        assert handler._query_bus == self.query_bus
        assert handler._command_bus == self.command_bus
        assert handler._scheduler_strategy == self.scheduler_strategy
        # Handler stores metrics as _metrics_collector
        assert handler._metrics_collector == self.metrics

    def test_request_machines_handler_initialization(self):
        """Test that RequestMachinesRESTHandler can be initialized with all dependencies."""
        handler = RequestMachinesRESTHandler(
            query_bus=self.query_bus,
            command_bus=self.command_bus,
            scheduler_strategy=self.scheduler_strategy,
            logger=self.logger,
            error_handler=self.error_handler,
            metrics=self.metrics,
        )

        assert handler._query_bus == self.query_bus
        assert handler._command_bus == self.command_bus
        assert handler._scheduler_strategy == self.scheduler_strategy
        assert handler.logger == self.logger
        assert handler.error_handler == self.error_handler
        assert handler._metrics_collector == self.metrics

    def test_get_request_status_handler_initialization(self):
        """Test that GetRequestStatusRESTHandler can be initialized with all dependencies."""
        handler = GetRequestStatusRESTHandler(
            query_bus=self.query_bus,
            command_bus=self.command_bus,
            scheduler_strategy=self.scheduler_strategy,
            logger=self.logger,
            error_handler=self.error_handler,
            metrics=self.metrics,
        )

        assert handler._query_bus == self.query_bus
        assert handler._command_bus == self.command_bus
        assert handler._scheduler_strategy == self.scheduler_strategy
        assert handler.logger == self.logger
        assert handler.error_handler == self.error_handler
        assert handler._metrics_collector == self.metrics

    def test_get_return_requests_handler_initialization(self):
        """Test that GetReturnRequestsRESTHandler can be initialized with all dependencies."""
        handler = GetReturnRequestsRESTHandler(
            query_bus=self.query_bus,
            command_bus=self.command_bus,
            scheduler_strategy=self.scheduler_strategy,
            config_manager=MagicMock(),
            logger=self.logger,
            error_handler=self.error_handler,
            metrics=self.metrics,
        )

        assert handler._query_bus == self.query_bus
        assert handler._command_bus == self.command_bus
        assert handler._scheduler_strategy == self.scheduler_strategy
        assert handler.logger == self.logger
        assert handler.error_handler == self.error_handler
        assert handler._metrics_collector == self.metrics

    def test_request_return_machines_handler_initialization(self):
        """Test that RequestReturnMachinesRESTHandler can be initialized with all dependencies."""
        handler = RequestReturnMachinesRESTHandler(
            query_bus=self.query_bus,
            command_bus=self.command_bus,
            scheduler_strategy=self.scheduler_strategy,
            logger=self.logger,
            error_handler=self.error_handler,
            metrics=self.metrics,
        )

        assert handler._query_bus == self.query_bus
        assert handler._command_bus == self.command_bus
        assert handler._scheduler_strategy == self.scheduler_strategy
        assert handler.logger == self.logger
        assert handler.error_handler == self.error_handler
        assert handler._metrics_collector == self.metrics


class TestAPIHandlerRegistration:
    """Test API handler registration in server_services.py."""

    @patch("orb.infrastructure.di.server_services._register_fastapi_services")
    @patch("orb.infrastructure.di.server_services._register_api_handlers")
    def test_register_server_services_with_fastapi(
        self, mock_register_api_handlers, mock_register_fastapi
    ):
        """Test that register_server_services calls handlers when server is enabled and FastAPI is available."""
        from orb.config.schemas.server_schema import ServerConfig
        from orb.infrastructure.di.server_services import register_server_services

        container = MagicMock()
        config_manager = MagicMock()
        server_config = MagicMock(spec=ServerConfig)
        server_config.enabled = True
        config_manager.get_typed.return_value = server_config
        container.get.return_value = config_manager

        register_server_services(container)

        mock_register_fastapi.assert_called_once_with(container, server_config)
        mock_register_api_handlers.assert_called_once_with(container)

    def test_register_server_services_without_fastapi(self):
        """Test that register_server_services raises when FastAPI is unavailable."""
        from orb.config.schemas.server_schema import ServerConfig
        from orb.infrastructure.di.server_services import register_server_services

        container = MagicMock()
        config_manager = MagicMock()
        server_config = MagicMock(spec=ServerConfig)
        server_config.enabled = True
        config_manager.get_typed.return_value = server_config
        container.get.return_value = config_manager

        with patch(
            "orb.infrastructure.di.server_services._register_fastapi_services"
        ) as mock_fastapi:
            mock_fastapi.side_effect = ImportError("No module named 'fastapi'")

            import pytest

            with pytest.raises(ImportError, match="No module named 'fastapi'"):
                register_server_services(container)

    @patch("orb.infrastructure.di.server_services._register_api_handlers")
    def test_register_server_services_disabled(self, mock_register_api_handlers):
        """Test that register_server_services doesn't call _register_api_handlers when server is disabled."""
        from orb.config.schemas.server_schema import ServerConfig
        from orb.infrastructure.di.server_services import register_server_services

        container = MagicMock()
        config_manager = MagicMock()
        server_config = MagicMock(spec=ServerConfig)
        server_config.enabled = False
        config_manager.get_typed.return_value = server_config
        container.get.return_value = config_manager

        register_server_services(container)

        mock_register_api_handlers.assert_not_called()
