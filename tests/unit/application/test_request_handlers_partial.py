"""Unit tests for request handler partial success behavior."""

from unittest.mock import AsyncMock, Mock

import pytest

from application.commands.request_handlers import (
    CreateMachineRequestHandler,
    CreateReturnRequestHandler,
)
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
        mock_uow.machines.save_batch.return_value = []
        mock_uow_factory.create_unit_of_work.return_value.__enter__ = Mock(return_value=mock_uow)
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

        mock_provider_selection.select_provider_for_template.return_value = ProviderSelectionResult(
            provider_type="aws",
            provider_instance="aws-default",
            selection_reason="test",
            confidence=0.9,
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
        assert (
            saved_request.metadata["fleet_errors"][0]["error_code"]
            == "InsufficientInstanceCapacity"
        )
        mock_uow.machines.save_batch.assert_called_once()
        saved_machines = mock_uow.machines.save_batch.call_args[0][0]
        assert len(saved_machines) == 2

    @pytest.mark.asyncio
    async def test_failure_status_preserves_provider_errors(self):
        mock_logger = Mock()
        mock_container = Mock()
        mock_event_publisher = Mock()
        mock_error_handler = Mock()

        mock_uow_factory = Mock()
        mock_uow = Mock()
        mock_uow.requests = Mock()
        mock_uow.machines = Mock()
        mock_uow.requests.save.return_value = []
        mock_uow.machines.save_batch.return_value = []
        mock_uow_factory.create_unit_of_work.return_value.__enter__ = Mock(return_value=mock_uow)
        mock_uow_factory.create_unit_of_work.return_value.__exit__ = Mock(return_value=None)

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
            provider_api="VMSS",
        )
        mock_query_bus.execute = AsyncMock(return_value=mock_template)

        mock_provider_selection = Mock(spec=ProviderSelectionService)
        from application.services.provider_selection_service import ProviderSelectionResult

        mock_provider_selection.select_provider_for_template.return_value = ProviderSelectionResult(
            provider_type="azure",
            provider_instance="azure-default",
            selection_reason="test",
            confidence=0.9,
        )

        mock_provider_capability = Mock(spec=ProviderCapabilityService)
        from application.services.provider_capability_service import ValidationResult

        mock_provider_capability.validate_template_requirements.return_value = ValidationResult(
            is_valid=True,
            provider_instance="azure-default",
            errors=[],
            warnings=[],
            supported_features=[],
            unsupported_features=[],
        )

        mock_provider_context = Mock(spec=ProviderContext)
        mock_provider_context.available_strategies = ["azure-azure-default"]

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

        handler._execute_provisioning = AsyncMock(
            return_value={
                "success": False,
                "resource_ids": [],
                "instances": [],
                    "provider_data": {
                        "fleet_errors": [
                            {
                                "error_code": "AllocationFailed",
                                "error_message": "Allocation failed in zone 1",
                            }
                        ]
                    },
                "error_message": "Failed to create instances: Allocation failed in zone 1",
            }
        )

        command = CreateRequestCommand(template_id="tmpl-1", requested_count=2)
        result = await handler.execute_command(command)

        assert isinstance(result, str)
        saved_request = mock_uow.requests.save.call_args[0][0]
        assert saved_request.status == RequestStatus.FAILED
        assert saved_request.metadata["fleet_errors"][0]["error_code"] == "AllocationFailed"

    @pytest.mark.asyncio
    async def test_cyclecloud_async_create_persists_operation_tracking_metadata(self):
        mock_logger = Mock()
        mock_container = Mock()
        mock_event_publisher = Mock()
        mock_error_handler = Mock()

        mock_uow_factory = Mock()
        mock_uow = Mock()
        mock_uow.requests = Mock()
        mock_uow.machines = Mock()
        mock_uow.requests.save.return_value = []
        mock_uow.machines.save_batch.return_value = []
        mock_uow_factory.create_unit_of_work.return_value.__enter__ = Mock(return_value=mock_uow)
        mock_uow_factory.create_unit_of_work.return_value.__exit__ = Mock(return_value=None)

        mock_query_bus = Mock(spec=QueryBus)
        from domain.template.template_aggregate import Template

        mock_template = Template(
            template_id="tmpl-1",
            name="Test Template",
            description="Test",
            instance_type="Standard_D4s_v5",
            image_id="image-123",
            max_instances=10,
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            provider_api="CycleCloud",
        )
        mock_query_bus.execute = AsyncMock(return_value=mock_template)

        mock_provider_selection = Mock(spec=ProviderSelectionService)
        from application.services.provider_selection_service import ProviderSelectionResult

        mock_provider_selection.select_provider_for_template.return_value = ProviderSelectionResult(
            provider_type="azure",
            provider_instance="azure-default",
            selection_reason="test",
            confidence=0.9,
        )

        mock_provider_capability = Mock(spec=ProviderCapabilityService)
        from application.services.provider_capability_service import ValidationResult

        mock_provider_capability.validate_template_requirements.return_value = ValidationResult(
            is_valid=True,
            provider_instance="azure-default",
            errors=[],
            warnings=[],
            supported_features=[],
            unsupported_features=[],
        )

        mock_provider_context = Mock(spec=ProviderContext)
        mock_provider_context.available_strategies = ["azure-azure-default"]

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

        handler._execute_provisioning = AsyncMock(
            return_value={
                "success": True,
                "resource_ids": ["my-cluster"],
                "instances": [],
                "provider_data": {
                    "handler_used": "CycleCloudHandler",
                    "cluster_name": "my-cluster",
                    "node_array": "execute",
                    "operation_id": "op-123",
                    "operation_location": "https://cc.example.com/operations/op-123",
                    "cyclecloud_url": "https://cc.example.com",
                    "cyclecloud_verify_ssl": False,
                },
                "error_message": None,
            }
        )

        command = CreateRequestCommand(template_id="tmpl-1", requested_count=2)
        result = await handler.execute_command(command)

        assert isinstance(result, str)
        saved_request = mock_uow.requests.save.call_args[0][0]
        assert saved_request.status == RequestStatus.IN_PROGRESS
        assert saved_request.status_message == "Resources created, instances pending"
        assert saved_request.provider_api == "CycleCloud"
        assert saved_request.metadata["cluster_name"] == "my-cluster"
        assert saved_request.metadata["node_array"] == "execute"
        assert saved_request.metadata["operation_id"] == "op-123"
        assert (
            saved_request.metadata["operation_location"]
            == "https://cc.example.com/operations/op-123"
        )
        assert saved_request.metadata["cyclecloud_url"] == "https://cc.example.com"
        assert saved_request.metadata["cyclecloud_verify_ssl"] is False
        mock_uow.machines.save_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_azure_async_create_persists_resource_group_metadata(self):
        mock_logger = Mock()
        mock_container = Mock()
        mock_event_publisher = Mock()
        mock_error_handler = Mock()

        mock_uow_factory = Mock()
        mock_uow = Mock()
        mock_uow.requests = Mock()
        mock_uow.machines = Mock()
        mock_uow.requests.save.return_value = []
        mock_uow.machines.save_batch.return_value = []
        mock_uow_factory.create_unit_of_work.return_value.__enter__ = Mock(return_value=mock_uow)
        mock_uow_factory.create_unit_of_work.return_value.__exit__ = Mock(return_value=None)

        mock_query_bus = Mock(spec=QueryBus)
        from domain.template.template_aggregate import Template

        mock_template = Template(
            template_id="tmpl-azure-rg",
            name="Azure VMSS Template",
            description="Test",
            instance_type="Standard_D4s_v5",
            image_id="image-123",
            max_instances=10,
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            provider_api="VMSS",
        )
        mock_query_bus.execute = AsyncMock(return_value=mock_template)

        mock_provider_selection = Mock(spec=ProviderSelectionService)
        from application.services.provider_selection_service import ProviderSelectionResult

        mock_provider_selection.select_provider_for_template.return_value = ProviderSelectionResult(
            provider_type="azure",
            provider_instance="azure-default",
            selection_reason="test",
            confidence=0.9,
        )

        mock_provider_capability = Mock(spec=ProviderCapabilityService)
        from application.services.provider_capability_service import ValidationResult

        mock_provider_capability.validate_template_requirements.return_value = ValidationResult(
            is_valid=True,
            provider_instance="azure-default",
            errors=[],
            warnings=[],
            supported_features=[],
            unsupported_features=[],
        )

        mock_provider_context = Mock(spec=ProviderContext)
        mock_provider_context.available_strategies = ["azure-azure-default"]

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

        handler._execute_provisioning = AsyncMock(
            return_value={
                "success": True,
                "resource_ids": ["vmss-demo"],
                "instances": [],
                "provider_data": {
                    "handler_used": "VMSSHandler",
                    "resource_group": "custom-rg",
                },
                "error_message": None,
            }
        )

        command = CreateRequestCommand(template_id="tmpl-azure-rg", requested_count=2)
        result = await handler.execute_command(command)

        assert isinstance(result, str)
        saved_request = mock_uow.requests.save.call_args[0][0]
        assert saved_request.provider_api == "VMSS"
        assert saved_request.metadata["resource_group"] == "custom-rg"
        assert saved_request.status == RequestStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_cyclecloud_return_forwards_bearer_auth_context(self):
        mock_logger = Mock()
        mock_container = Mock()
        mock_event_publisher = Mock()
        mock_error_handler = Mock()
        mock_uow_factory = Mock()
        mock_uow = Mock()
        mock_uow_factory.create_unit_of_work.return_value.__enter__ = Mock(return_value=mock_uow)
        mock_uow_factory.create_unit_of_work.return_value.__exit__ = Mock(return_value=None)

        mock_query_bus = Mock(spec=QueryBus)
        mock_query_bus.execute = AsyncMock(
            return_value=Mock(provider_api="CycleCloud")
        )

        scheduler = Mock()
        scheduler.format_template_for_provider.return_value = {
            "cyclecloud_url": "https://cc.example.com",
            "cyclecloud_auth_mode": "bearer",
            "cyclecloud_aad_scope": "https://cc.example.com/.default",
        }
        mock_container.get.return_value = scheduler

        mock_provider_context = Mock(spec=ProviderContext)
        mock_provider_context.terminate_resources = AsyncMock(
            return_value={"success": True, "error_message": None}
        )

        handler = CreateReturnRequestHandler(
            uow_factory=mock_uow_factory,
            logger=mock_logger,
            container=mock_container,
            event_publisher=mock_event_publisher,
            error_handler=mock_error_handler,
            provider_port=mock_provider_context,
            query_bus=mock_query_bus,
        )

        request = Mock()
        request.request_id = "ret-12345678-1234-1234-1234-123456789012"
        request.metadata = {}

        result = await handler._process_template_group(
            template_id="tmpl-1",
            instance_group=["node-1"],
            request=request,
            resource_mapping={"node-1": ("my-cluster", 1)},
        )

        assert result["success"] is True
        operation = mock_provider_context.terminate_resources.call_args.args[1]
        assert operation.context["cyclecloud_url"] == "https://cc.example.com"
        assert operation.context["cyclecloud_auth_mode"] == "bearer"
        assert operation.context["cyclecloud_aad_scope"] == "https://cc.example.com/.default"
