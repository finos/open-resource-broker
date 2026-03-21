"""Tests for orchestrator initialization (replaces deleted API handler tests)."""

from unittest.mock import MagicMock

from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
from orb.application.services.orchestration.get_machine import GetMachineOrchestrator
from orb.application.services.orchestration.get_request_status import GetRequestStatusOrchestrator
from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
from orb.application.services.orchestration.list_return_requests import (
    ListReturnRequestsOrchestrator,
)
from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
from orb.domain.base.ports.logging_port import LoggingPort
from orb.infrastructure.di.buses import CommandBus, QueryBus


class TestOrchestratorInitialization:
    """Test orchestrator initialization."""

    def setup_method(self):
        """Set up test dependencies."""
        self.command_bus = MagicMock(spec=CommandBus)
        self.query_bus = MagicMock(spec=QueryBus)
        self.logger = MagicMock(spec=LoggingPort)

    def _make(self, cls):
        return cls(
            command_bus=self.command_bus,
            query_bus=self.query_bus,
            logger=self.logger,
        )

    def test_acquire_machines_orchestrator_init(self):
        o = self._make(AcquireMachinesOrchestrator)
        assert o._command_bus is self.command_bus
        assert o._query_bus is self.query_bus
        assert o._logger is self.logger

    def test_get_request_status_orchestrator_init(self):
        o = self._make(GetRequestStatusOrchestrator)
        assert o._command_bus is self.command_bus
        assert o._query_bus is self.query_bus

    def test_list_requests_orchestrator_init(self):
        o = self._make(ListRequestsOrchestrator)
        assert o._command_bus is self.command_bus

    def test_return_machines_orchestrator_init(self):
        o = self._make(ReturnMachinesOrchestrator)
        assert o._command_bus is self.command_bus

    def test_cancel_request_orchestrator_init(self):
        o = self._make(CancelRequestOrchestrator)
        assert o._command_bus is self.command_bus

    def test_list_machines_orchestrator_init(self):
        o = self._make(ListMachinesOrchestrator)
        assert o._query_bus is self.query_bus

    def test_get_machine_orchestrator_init(self):
        o = self._make(GetMachineOrchestrator)
        assert o._query_bus is self.query_bus

    def test_list_templates_orchestrator_init(self):
        o = self._make(ListTemplatesOrchestrator)
        assert o._query_bus is self.query_bus

    def test_list_return_requests_orchestrator_init(self):
        o = self._make(ListReturnRequestsOrchestrator)
        assert o._query_bus is self.query_bus


class TestAPIHandlerRegistration:
    """Test server_services registration."""

    def test_register_server_services_enabled(self):
        """register_server_services calls _register_orchestrators when server is enabled."""
        from unittest.mock import patch

        from orb.bootstrap.server_services import register_server_services
        from orb.config.schemas.server_schema import ServerConfig

        container = MagicMock()
        config_manager = MagicMock()
        server_config = MagicMock(spec=ServerConfig)
        server_config.enabled = True
        config_manager.get_typed.return_value = server_config
        container.get.return_value = config_manager

        with (
            patch("orb.bootstrap.server_services._register_fastapi_services") as mock_fastapi,
            patch("orb.bootstrap.server_services._register_orchestrators") as mock_orch,
        ):
            register_server_services(container)
            mock_fastapi.assert_called_once_with(container, server_config)
            mock_orch.assert_called_once_with(container)

    def test_register_server_services_disabled(self):
        """register_server_services skips registration when server is disabled."""
        from unittest.mock import patch

        from orb.bootstrap.server_services import register_server_services
        from orb.config.schemas.server_schema import ServerConfig

        container = MagicMock()
        config_manager = MagicMock()
        server_config = MagicMock(spec=ServerConfig)
        server_config.enabled = False
        config_manager.get_typed.return_value = server_config
        container.get.return_value = config_manager

        with patch("orb.bootstrap.server_services._register_orchestrators") as mock_orch:
            register_server_services(container)
            mock_orch.assert_not_called()
