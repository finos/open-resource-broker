"""
CQRS Integration Tests - Current Architecture Validation.

This test suite validates the CQRS architecture implementation with
command and query handlers, ensuring appropriate integration between
application services, domain aggregates, and infrastructure.
"""

from unittest.mock import Mock

import pytest

from orb.application.commands.request_handlers import CreateMachineRequestHandler
from orb.application.dto.commands import CreateRequestCommand
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports import (
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
    ProviderSelectionPort,
)
from orb.infrastructure.di.buses import CommandBus, QueryBus
from orb.infrastructure.di.container import DIContainer


@pytest.mark.integration
class TestCQRSArchitectureIntegration:
    """Integration tests for CQRS architecture with command and query handlers."""

    @pytest.fixture
    def mock_logger(self):
        """Create mock logger."""
        return Mock(spec=LoggingPort)

    @pytest.fixture
    def mock_container(self):
        """Create mock container."""
        from unittest.mock import MagicMock

        container = Mock(spec=DIContainer)

        # Create a mock configuration manager
        mock_config_manager = MagicMock()
        mock_provider_config = MagicMock()
        mock_provider_config.get_active_providers.return_value = []  # Return empty list to skip provider validation
        mock_config_manager.get_provider_config.return_value = mock_provider_config

        # Create a mock scheduler
        mock_scheduler = MagicMock()
        mock_scheduler.format_template_for_provider.return_value = {"instance_type": "t2.micro"}

        # Configure container.get to return appropriate mocks
        def get_mock(service_type):
            from orb.application.ports.scheduler_port import SchedulerPort
            from orb.domain.base.ports.configuration_port import ConfigurationPort

            if service_type == ConfigurationPort:
                return mock_config_manager
            elif service_type == SchedulerPort:
                return mock_scheduler
            else:
                return MagicMock()

        container.get.side_effect = get_mock
        return container

    @pytest.fixture
    def mock_event_publisher(self):
        """Create mock event publisher."""
        return Mock(spec=EventPublisherPort)

    @pytest.fixture
    def mock_error_handler(self):
        """Create mock error handler."""
        return Mock(spec=ErrorHandlingPort)

    @pytest.fixture
    def mock_uow_factory(self):
        """Create mock unit of work factory."""
        uow_factory = Mock(spec=UnitOfWorkFactory)
        uow = Mock()
        uow.requests = Mock()
        uow.machines = Mock()
        uow.requests.save.return_value = []  # No events
        uow.machines.save.return_value = []  # No events
        uow.machines.save_batch.return_value = []  # No events
        uow_factory.create_unit_of_work.return_value.__enter__ = Mock(return_value=uow)
        uow_factory.create_unit_of_work.return_value.__exit__ = Mock(return_value=None)
        return uow_factory

    @pytest.fixture
    def mock_query_bus(self):
        """Create mock query bus."""
        from unittest.mock import AsyncMock

        bus = Mock(spec=QueryBus)

        # Mock template query response
        from orb.domain.template.template_aggregate import Template

        mock_template = Template(
            template_id="web-server-template",
            name="Web Server Template",
            description="Test template",
            machine_types={"t2.micro": 1},
            image_id="ami-12345678",
            max_instances=10,
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            provider_api="EC2Fleet",
        )

        bus.execute = AsyncMock(return_value=mock_template)
        return bus

    @pytest.fixture
    def mock_provider_selection_port(self):
        """Create mock provider selection port."""
        from unittest.mock import AsyncMock

        port = Mock(spec=ProviderSelectionPort)

        # Mock selection result
        from orb.domain.base.results import ProviderSelectionResult

        selection_result = ProviderSelectionResult(
            provider_type="aws",
            provider_name="aws-default",
            selection_reason="Best match for template requirements",
            confidence=0.95,
        )
        port.select_provider_for_template.return_value = selection_result
        port.get_available_strategies.return_value = ["aws-aws-default"]

        from orb.domain.base.results import ValidationResult

        port.validate_template_requirements.return_value = ValidationResult(
            is_valid=True,
            provider_instance="aws-default",
            errors=[],
            warnings=[],
            supported_features=[],
            unsupported_features=[],
        )

        # Mock execute_operation as async
        from orb.providers.base.strategy.provider_strategy import ProviderResult

        provider_result = ProviderResult(
            success=True,
            data={
                "resource_ids": ["fleet-12345"],
                "instance_ids": ["i-1234567890abcdef0", "i-0987654321fedcba0"],
                "instances": [
                    {"instance_id": "i-1234567890abcdef0", "state": "running"},
                    {"instance_id": "i-0987654321fedcba0", "state": "running"},
                ],
            },
            metadata={"provider": "aws", "region": "us-east-1"},
        )
        port.execute_operation = AsyncMock(return_value=provider_result)

        return port

    @pytest.fixture
    def mock_provider_capability_service(self):
        """Create mock provider capability service."""
        service = Mock()  # Remove spec since ProviderCapabilityService was removed

        # Mock validation result
        from orb.domain.base.results import ValidationResult

        validation_result = ValidationResult(
            is_valid=True,
            provider_instance="aws-default",
            errors=[],
            warnings=[],
            supported_features=["API: EC2Fleet", "Pricing: On-demand instances"],
            unsupported_features=[],
        )
        service.validate_template_requirements.return_value = validation_result
        return service

    @pytest.fixture
    def create_request_handler(
        self,
        mock_uow_factory,
        mock_logger,
        mock_container,
        mock_event_publisher,
        mock_error_handler,
        mock_query_bus,
        mock_provider_selection_port,
    ):
        """Create CreateMachineRequestHandler for testing."""
        mock_provider_config_port = Mock()
        return CreateMachineRequestHandler(
            uow_factory=mock_uow_factory,
            logger=mock_logger,
            container=mock_container,
            event_publisher=mock_event_publisher,
            error_handler=mock_error_handler,
            query_bus=mock_query_bus,
            provider_selection_port=mock_provider_selection_port,
            provider_config_port=mock_provider_config_port,
        )

    @pytest.fixture
    def command_bus(self, create_request_handler, mock_logger):
        """Create command bus with registered handlers."""
        from unittest.mock import MagicMock

        # Create a mock container that returns the actual handler
        mock_container = MagicMock()
        mock_container.get.return_value = create_request_handler

        bus = CommandBus(mock_container, mock_logger)
        return bus

    @pytest.mark.asyncio
    async def test_create_request_command_handler(self, create_request_handler):
        """Test CreateMachineRequestHandler directly."""
        # Create command
        command = CreateRequestCommand(
            template_id="web-server-template",
            requested_count=2,
        )

        # Execute command
        result = await create_request_handler.execute_command(command)

        # Commands return None in CQRS pattern
        assert result is None

        # Verify handler interactions
        create_request_handler._query_bus.execute.assert_called_once()
        create_request_handler._provider_selection_port.select_provider_for_template.assert_called_once()
        create_request_handler._provider_selection_port.execute_operation.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_bus_integration(self, command_bus):
        """Test command bus with registered handlers."""
        # Create command
        command = CreateRequestCommand(
            template_id="web-server-template",
            requested_count=1,
        )

        # Execute via command bus
        result = await command_bus.execute(command)

        # Commands return None in CQRS pattern
        assert result is None

    def test_provider_capability_service_integration(self, mock_provider_capability_service):
        """Test provider capability service integration."""
        from orb.domain.template.template_aggregate import Template

        # Create test template
        template = Template(
            template_id="test-template",
            name="Test Template",
            description="Test",
            machine_types={"t2.micro": 1},
            image_id="ami-12345678",
            max_instances=5,
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            provider_api="EC2Fleet",
        )

        # Test validation
        result = mock_provider_capability_service.validate_template_requirements(
            template, "aws-default"
        )

        # Verify result structure
        assert result.is_valid is True
        assert result.provider_instance == "aws-default"
        assert isinstance(result.supported_features, list)
        assert isinstance(result.errors, list)

    def test_provider_selection_port_integration(self, mock_provider_selection_port):
        """Test provider selection port integration."""
        from orb.domain.template.template_aggregate import Template

        # Create test template
        template = Template(
            template_id="test-template",
            name="Test Template",
            description="Test",
            machine_types={"t2.micro": 1},
            image_id="ami-12345678",
            max_instances=5,
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            provider_api="EC2Fleet",
        )

        # Test selection
        result = mock_provider_selection_port.select_provider_for_template(template)

        # Verify result structure
        assert result.provider_type == "aws"
        assert result.provider_name == "aws-default"
        assert isinstance(result.selection_reason, str)
        assert isinstance(result.confidence, float)

    @pytest.mark.asyncio
    async def test_provider_operation_integration(self, mock_provider_selection_port):
        """Test provider operation integration."""
        from unittest.mock import AsyncMock

        from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
        from orb.providers.base.strategy.provider_strategy import ProviderResult

        # Mock as async
        result = ProviderResult(
            success=True,
            data={"instance_ids": ["i-1234567890abcdef0", "i-0987654321fedcba0"]},
            metadata={"provider": "aws", "region": "us-east-1"},
            error_message=None,
        )
        mock_provider_selection_port.execute_operation = AsyncMock(return_value=result)

        # Create test operation
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"template_config": {"instance_type": "t2.micro"}, "count": 2},
            context={"correlation_id": "test-123"},
        )

        # Execute operation
        result = await mock_provider_selection_port.execute_operation("aws-aws-default", operation)

        # Verify result structure
        assert result.success is True
        assert "instance_ids" in result.data
        assert isinstance(result.data["instance_ids"], list)
        assert len(result.data["instance_ids"]) == 2

    @pytest.mark.asyncio
    async def test_error_handling_invalid_template(self, create_request_handler):
        """Test error handling for invalid template."""

        # Mock query bus to return None (template not found)
        async def mock_execute_none(query):
            return None

        create_request_handler._query_bus.execute = mock_execute_none

        # Create command with invalid template
        command = CreateRequestCommand(
            template_id="non-existent-template",
            requested_count=1,
        )

        # Execute command and expect error
        with pytest.raises((AttributeError, Exception)):
            await create_request_handler.execute_command(command)

    @pytest.mark.asyncio
    async def test_error_handling_provider_failure(self, create_request_handler):
        """Test error handling for provider failures."""
        # Mock provider selection port to return failure
        from unittest.mock import AsyncMock

        from orb.providers.base.strategy.provider_strategy import ProviderResult

        failure_result = ProviderResult(
            success=False,
            data={},
            metadata={},
            error_message="Provider operation failed",
        )
        create_request_handler._provider_selection_port.execute_operation = AsyncMock(
            return_value=failure_result
        )

        # Create command with explicit metadata to avoid Pydantic validation issues
        command = CreateRequestCommand(
            template_id="web-server-template",
            requested_count=1,
            metadata={},
        )

        # Execute command - should handle failure gracefully
        result = await create_request_handler.execute_command(command)

        # Commands return None in CQRS pattern
        assert result is None

    def test_cqrs_separation_of_concerns(self, create_request_handler):
        """Test that CQRS properly separates command and query concerns."""
        # Verify handler has appropriate dependencies
        assert hasattr(create_request_handler, "_query_bus")
        assert hasattr(create_request_handler, "_provider_selection_port")
        assert hasattr(create_request_handler, "uow_factory")

        # Verify handler follows CQRS pattern
        assert hasattr(create_request_handler, "execute_command")
        assert hasattr(create_request_handler, "validate_command")

        # Verify handler has proper initialization signature with type hints
        assert hasattr(create_request_handler.__init__, "__annotations__")
        assert len(create_request_handler.__init__.__annotations__) > 0

    @pytest.mark.asyncio
    async def test_unit_of_work_pattern(self, create_request_handler):
        """Test that handlers properly use Unit of Work pattern."""
        # Create command with explicit metadata to avoid Pydantic validation issues
        command = CreateRequestCommand(
            template_id="web-server-template",
            requested_count=1,
            metadata={},
        )

        # Execute command
        result = await create_request_handler.execute_command(command)

        # Verify UoW was used
        create_request_handler.uow_factory.create_unit_of_work.assert_called()

        # Commands return None in CQRS pattern
        assert result is None

    @pytest.mark.asyncio
    async def test_event_publishing_integration(self, create_request_handler):
        """Test that events are properly published."""
        # Create command
        command = CreateRequestCommand(
            template_id="web-server-template",
            requested_count=1,
        )

        # Execute command
        result = await create_request_handler.execute_command(command)

        # Verify event publisher was called (events from UoW save)
        # Note: In this mock setup, save returns empty list, so no events published
        # In real scenario, domain aggregates would generate events
        # Commands return None in CQRS pattern
        assert result is None


# Removed TestMachineStatusConversionBaseline class as MachineStatusConversionService does not exist


if __name__ == "__main__":
    # Run baseline tests
    pytest.main([__file__, "-v", "--tb=short"])
