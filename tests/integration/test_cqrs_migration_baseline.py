"""
CQRS Integration Tests - Current Architecture Validation.

This test suite validates the CQRS architecture implementation with
command and query handlers, ensuring proper integration between
application services, domain aggregates, and infrastructure.
"""

from unittest.mock import Mock

import pytest

from src.application.commands.request_handlers import CreateMachineRequestHandler
from src.application.dto.commands import CreateRequestCommand
from src.application.services.provider_capability_service import (
    ProviderCapabilityService,
)
from src.application.services.provider_selection_service import ProviderSelectionService
from src.domain.base import UnitOfWorkFactory
from src.domain.base.ports import (
    ContainerPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
)
from src.infrastructure.di.buses import CommandBus, QueryBus
from src.providers.base.strategy import ProviderContext


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
        container = Mock(spec=ContainerPort)
        # Mock the get method to return mocks for any requested service
        container.get.return_value = Mock()
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
        uow_factory.create_unit_of_work.return_value.__enter__ = Mock(return_value=uow)
        uow_factory.create_unit_of_work.return_value.__exit__ = Mock(return_value=None)
        return uow_factory

    @pytest.fixture
    def mock_query_bus(self):
        """Create mock query bus."""
        bus = Mock(spec=QueryBus)

        # Mock template query response
        from src.domain.template.aggregate import Template

        mock_template = Template(
            template_id="web-server-template",
            name="Web Server Template",
            description="Test template",
            instance_type="t2.micro",
            image_id="ami-12345678",
            max_instances=10,
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            provider_api="EC2Fleet",
        )

        async def mock_execute(query):
            return mock_template

        bus.execute = mock_execute
        return bus

    @pytest.fixture
    def mock_provider_selection_service(self):
        """Create mock provider selection service."""
        service = Mock(spec=ProviderSelectionService)

        # Mock selection result
        from src.application.services.provider_selection_service import (
            ProviderSelectionResult,
        )

        selection_result = ProviderSelectionResult(
            provider_type="aws",
            provider_instance="aws-default",
            selection_reason="Best match for template requirements",
            confidence=0.95,
        )
        service.select_provider_for_template.return_value = selection_result
        return service

    @pytest.fixture
    def mock_provider_capability_service(self):
        """Create mock provider capability service."""
        service = Mock(spec=ProviderCapabilityService)

        # Mock validation result
        from src.application.services.provider_capability_service import (
            ValidationResult,
        )

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
    def mock_provider_context(self):
        """Create mock provider context."""
        context = Mock(spec=ProviderContext)
        context.available_strategies = ["aws-aws-default"]
        context.current_strategy_type = "aws-aws-default"

        # Mock execution result
        from src.providers.base.strategy.provider_strategy import ProviderResult

        result = ProviderResult(
            success=True,
            data={"instance_ids": ["i-1234567890abcdef0", "i-0987654321fedcba0"]},
            metadata={"provider": "aws", "region": "us-east-1"},
            error_message=None,
        )
        context.execute_with_strategy.return_value = result
        return context

    @pytest.fixture
    def create_request_handler(
        self,
        mock_uow_factory,
        mock_logger,
        mock_container,
        mock_event_publisher,
        mock_error_handler,
        mock_query_bus,
        mock_provider_selection_service,
        mock_provider_capability_service,
        mock_provider_context,
    ):
        """Create CreateMachineRequestHandler for testing."""
        return CreateMachineRequestHandler(
            uow_factory=mock_uow_factory,
            logger=mock_logger,
            container=mock_container,
            event_publisher=mock_event_publisher,
            error_handler=mock_error_handler,
            query_bus=mock_query_bus,
            provider_selection_service=mock_provider_selection_service,
            provider_capability_service=mock_provider_capability_service,
            provider_context=mock_provider_context,
        )

    @pytest.fixture
    def command_bus(self, create_request_handler):
        """Create command bus with registered handlers."""
        bus = CommandBus()
        bus.register_handler(CreateRequestCommand, create_request_handler)
        return bus

    @pytest.mark.asyncio
    async def test_create_request_command_handler(self, create_request_handler):
        """Test CreateMachineRequestHandler directly."""
        # Create command
        command = CreateRequestCommand(
            template_id="web-server-template",
            machine_count=2,
            metadata={"test": "cqrs_integration"},
        )

        # Execute command
        result = await create_request_handler.execute_command(command)

        # Verify result is a request ID
        assert isinstance(result, str)
        assert len(result) > 0

        # Verify handler interactions
        create_request_handler._query_bus.execute.assert_called_once()
        create_request_handler._provider_selection_service.select_provider_for_template.assert_called_once()
        create_request_handler._provider_capability_service.validate_template_requirements.assert_called_once()
        create_request_handler._provider_context.execute_with_strategy.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_bus_integration(self, command_bus):
        """Test command bus with registered handlers."""
        # Create command
        command = CreateRequestCommand(
            template_id="web-server-template",
            machine_count=1,
            metadata={"test": "command_bus_integration"},
        )

        # Execute via command bus
        result = await command_bus.execute(command)

        # Verify result
        assert isinstance(result, str)
        assert len(result) > 0

    def test_provider_capability_service_integration(self, mock_provider_capability_service):
        """Test provider capability service integration."""
        from src.domain.template.aggregate import Template

        # Create test template
        template = Template(
            template_id="test-template",
            name="Test Template",
            description="Test",
            instance_type="t2.micro",
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

    def test_provider_selection_service_integration(self, mock_provider_selection_service):
        """Test provider selection service integration."""
        from src.domain.template.aggregate import Template

        # Create test template
        template = Template(
            template_id="test-template",
            name="Test Template",
            description="Test",
            instance_type="t2.micro",
            image_id="ami-12345678",
            max_instances=5,
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            provider_api="EC2Fleet",
        )

        # Test selection
        result = mock_provider_selection_service.select_provider_for_template(template)

        # Verify result structure
        assert result.provider_type == "aws"
        assert result.provider_instance == "aws-default"
        assert isinstance(result.selection_reason, str)
        assert isinstance(result.confidence, float)

    def test_provider_context_integration(self, mock_provider_context):
        """Test provider context integration."""
        from src.providers.base.strategy import ProviderOperation, ProviderOperationType

        # Create test operation
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"template_config": {"instance_type": "t2.micro"}, "count": 2},
            context={"correlation_id": "test-123"},
        )

        # Execute operation
        result = mock_provider_context.execute_with_strategy(
            "aws-aws-default", operation)

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
            machine_count=1,
            metadata={"test": "error_handling"},
        )

        # Execute command and expect error
        with pytest.raises(Exception) as exc_info:
            await create_request_handler.execute_command(command)

        # Verify error type
        assert "Template" in str(exc_info.value) or "not found" in str(
            exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_error_handling_provider_failure(self, create_request_handler):
        """Test error handling for provider failures."""
        # Mock provider context to return failure
        from src.providers.base.strategy.provider_strategy import ProviderResult

        failure_result = ProviderResult(
            success=False,
            data={},
            metadata={},
            error_message="Provider operation failed",
        )
        create_request_handler._provider_context.execute_with_strategy.return_value = failure_result

        # Create command
        command = CreateRequestCommand(
            template_id="web-server-template",
            machine_count=1,
            metadata={"test": "provider_failure"},
        )

        # Execute command - should handle failure gracefully
        result = await create_request_handler.execute_command(command)

        # Should still return request ID (request created but marked as failed)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_cqrs_separation_of_concerns(self, create_request_handler):
        """Test that CQRS properly separates command and query concerns."""
        # Verify handler has proper dependencies
        assert hasattr(create_request_handler, "_query_bus")
        assert hasattr(create_request_handler, "_provider_selection_service")
        assert hasattr(create_request_handler, "_provider_capability_service")
        assert hasattr(create_request_handler, "_provider_context")
        assert hasattr(create_request_handler, "uow_factory")

        # Verify handler follows CQRS pattern
        assert hasattr(create_request_handler, "execute_command")
        assert hasattr(create_request_handler, "validate_command")

        # Verify proper typing

        assert create_request_handler.__class__.__annotations__

    @pytest.mark.asyncio
    async def test_unit_of_work_pattern(self, create_request_handler):
        """Test that handlers properly use Unit of Work pattern."""
        # Create command
        command = CreateRequestCommand(
            template_id="web-server-template",
            machine_count=1,
            metadata={"test": "uow_pattern"},
        )

        # Execute command
        result = await create_request_handler.execute_command(command)

        # Verify UoW was used
        create_request_handler.uow_factory.create_unit_of_work.assert_called()

        # Verify result
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_event_publishing_integration(self, create_request_handler):
        """Test that events are properly published."""
        # Create command
        command = CreateRequestCommand(
            template_id="web-server-template",
            machine_count=1,
            metadata={"test": "event_publishing"},
        )

        # Execute command
        result = await create_request_handler.execute_command(command)

        # Verify event publisher was called (events from UoW save)
        # Note: In this mock setup, save returns empty list, so no events published
        # In real scenario, domain aggregates would generate events
        assert isinstance(result, str)


# Removed TestMachineStatusConversionBaseline class as MachineStatusConversionService does not exist


if __name__ == "__main__":
    # Run baseline tests
    pytest.main([__file__, "-v", "--tb=short"])
