"""Unit tests for request handler partial success behavior."""

from unittest.mock import AsyncMock, Mock

import pytest

from application.commands.request_handlers import CreateMachineRequestHandler
from application.dto.commands import CreateRequestCommand
from application.services.provider_capability_service import ProviderCapabilityService
from application.services.provider_selection_service import ProviderSelectionService
from domain.request.request_types import RequestStatus
from infrastructure.di.buses import QueryBus
from providers.base.strategy import ProviderContext


@pytest.mark.unit
class TestCreateMachineRequestHandlerPartial:
    """Validate partial status when API errors are returned with instances."""

    @pytest.mark.asyncio
    async def test_partial_status_when_instances_and_errors(self):
        """Request should be marked PARTIAL when instances are created with API errors."""
        # Mock logger and dependencies
        mock_logger = Mock()
        mock_container = Mock()
        mock_event_publisher = Mock()
        mock_error_handler = Mock()

        # Mock UnitOfWork
        mock_uow_factory = Mock()
        mock_uow = Mock()
        mock_uow.requests = Mock()
        mock_uow.machines = Mock()
        mock_uow.requests.save.return_value = []
        mock_uow.machines.save.return_value = []
        mock_uow_factory.create_unit_of_work.return_value.__enter__ = Mock(
            return_value=mock_uow
        )
        mock_uow_factory.create_unit_of_work.return_value.__exit__ = Mock(return_value=None)

        # Mock QueryBus to return a template
        mock_query_bus = Mock(spec=QueryBus)
        from domain.template.template_aggregate import Template

        mock_template = Template(
            template_id="tmpl-1",
            name="Test Template",
            description="Test",
            instance_type="t2.micro",
            image_id="ami-12345678",
            max_instances=10,
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            provider_api="EC2Fleet",
        )
        mock_query_bus.execute = AsyncMock(return_value=mock_template)

        # Mock provider selection and capability services
        mock_provider_selection = Mock(spec=ProviderSelectionService)
        from application.services.provider_selection_service import ProviderSelectionResult

        mock_provider_selection.select_provider_for_template.return_value = (
            ProviderSelectionResult(
                provider_type="aws",
                provider_instance="aws-default",
                selection_reason="test",
                confidence=0.9,
            )
        )

        mock_provider_capability = Mock(spec=ProviderCapabilityService)
        from application.services.provider_capability_service import ValidationResult

        mock_provider_capability.validate_template_requirements.return_value = ValidationResult(
            is_valid=True,
            provider_instance="aws-default",
            errors=[],
            warnings=[],
            supported_features=[],
            unsupported_features=[],
        )

        # Mock provider context
        mock_provider_context = Mock(spec=ProviderContext)
        mock_provider_context.available_strategies = ["aws-aws-default"]

        handler = CreateMachineRequestHandler(
            uow_factory=mock_uow_factory,
            logger=mock_logger,
            container=mock_container,
            event_publisher=mock_event_publisher,
            error_handler=mock_error_handler,
            query_bus=mock_query_bus,
            provider_selection_service=mock_provider_selection,
            provider_capability_service=mock_provider_capability,
            provider_port=mock_provider_context,
        )

        async def fake_execute_provisioning(template, request, selection_result):
            request.metadata["fleet_errors"] = [
                {
                    "error_code": "InsufficientInstanceCapacity",
                    "error_message": "Insufficient capacity.",
                }
            ]
            return {
                "success": True,
                "resource_ids": ["fleet-123"],
                "instances": [
                    {"instance_id": "i-123", "instance_type": "t2.micro"},
                    {"instance_id": "i-456", "instance_type": "t2.micro"},
                ],
                "provider_data": {},
                "error_message": None,
            }

        handler._execute_provisioning = AsyncMock(side_effect=fake_execute_provisioning)

        # Execute command
        command = CreateRequestCommand(template_id="tmpl-1", requested_count=2)
        result = await handler.execute_command(command)

        assert isinstance(result, str)
        saved_request = mock_uow.requests.save.call_args[0][0]
        assert saved_request.status == RequestStatus.PARTIAL
        assert saved_request.metadata["fleet_errors"][0]["error_code"] == "InsufficientInstanceCapacity"
        assert mock_uow.machines.save.call_count == 2
