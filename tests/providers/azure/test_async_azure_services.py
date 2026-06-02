"""Direct async coverage for Azure async service entry points."""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions.azure_exceptions import AzureValidationError
from orb.providers.azure.infrastructure.services.azure_deployment_service import (
    AzureDeploymentService,
)
from orb.providers.azure.services.health_check_service import AzureHealthCheckService
from orb.providers.azure.services.inventory_service import AzureInventoryService
from orb.providers.azure.services.inventory_service import AzureReadOperationContext
from orb.providers.azure.services.provisioning_service import (
    AzureProvisioningService,
    CreateOperationContext,
)
from orb.providers.azure.services.termination_service import (
    AzureTerminationService,
)
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from tests.providers.azure.strategy_test_support import make_azure_template

if TYPE_CHECKING:
    from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy


def _make_template(**overrides):
    return make_azure_template(
        template_id="azure-service-test",
        provider_api="SingleVM",
        ssh_public_keys=["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABg service@test"],
        **overrides,
    )


def _handler_provider(handler) -> "AzureProviderStrategy":
    """Build a strategy stand-in that returns ``handler`` from any ``resolve_handler`` call.

    Cast to ``AzureProviderStrategy`` so the test fixture satisfies the inventory
    service's annotated dependency without spinning up a real strategy. Duck-typed
    at runtime via ``SimpleNamespace.resolve_handler``.
    """
    return cast(
        "AzureProviderStrategy",
        SimpleNamespace(resolve_handler=lambda *_args, **_kwargs: handler),
    )


def _termination_operation(
    *,
    instance_ids: list[str],
    resource_mapping: dict[str, tuple[str, int]] | None = None,
    resource_id: str | None = None,
) -> ProviderOperation:
    parameters = {
        "instance_ids": instance_ids,
        "provider_api": "VMSS",
    }
    if resource_mapping is not None:
        parameters["resource_mapping"] = resource_mapping
    if resource_id is not None:
        parameters["resource_id"] = resource_id

    return ProviderOperation(
        operation_type=ProviderOperationType.TERMINATE_INSTANCES,
        parameters=parameters,
    )


async def _terminate_with_handler(
    handler,
    *,
    instance_ids: list[str],
    resource_mapping: dict[str, tuple[str, int]] | None = None,
    resource_id: str | None = None,
    logger: MagicMock | None = None,
    cleanup_recorder: MagicMock | None = None,
):
    service = AzureTerminationService(
        logger=logger or MagicMock(),
        handler_provider=_handler_provider(handler),
        record_pending_cleanup=cleanup_recorder or MagicMock(),
        default_resource_group="test-rg",
    )
    return await service.terminate_instances_async(
        _termination_operation(
            instance_ids=instance_ids,
            resource_mapping=resource_mapping,
            resource_id=resource_id,
        ),
        is_dry_run=False,
    )


@pytest.fixture
def resource_metadata_service() -> MagicMock:
    service = MagicMock()
    service.augment_vmss_capacity_metadata_async = AsyncMock()
    service.augment_single_vm_deployment_metadata_async = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_dispatch_collects_provider_data_and_records_cleanup():
    cleanup_recorder = MagicMock()
    handler = MagicMock()
    handler.release_hosts_async = AsyncMock(
        return_value={"provider_data": {"operation_status": "submitted"}}
    )

    result = await _terminate_with_handler(
        handler=handler,
        instance_ids=["vm-1"],
        resource_mapping={"vm-1": ("vmss-1", 1)},
        cleanup_recorder=cleanup_recorder,
    )

    assert result.success is True
    assert result.metadata["provider_data"]["termination_requests"] == [
        {"operation_status": "submitted"}
    ]
    cleanup_recorder.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_fans_out_across_multiple_resource_groups():
    cleanup_recorder = MagicMock()
    handler = MagicMock()
    handler.release_hosts_async = AsyncMock(
        side_effect=[
            {"provider_data": {"resource_id": "vmss-1"}},
            {"provider_data": {"resource_id": "vmss-2"}},
        ]
    )

    result = await _terminate_with_handler(
        handler=handler,
        instance_ids=["vm-1", "vm-2"],
        resource_mapping={"vm-1": ("vmss-1", 1), "vm-2": ("vmss-2", 1)},
        cleanup_recorder=cleanup_recorder,
    )

    assert result.success is True
    assert result.metadata["provider_data"]["termination_requests"] == [
        {"resource_id": "vmss-1"},
        {"resource_id": "vmss-2"},
    ]
    assert handler.release_hosts_async.await_count == 2
    cleanup_recorder.assert_called()


@pytest.mark.asyncio
async def test_dispatch_preserves_group_order_when_release_calls_complete_out_of_order():
    cleanup_recorder = MagicMock()
    handler = MagicMock()

    async def release_hosts_async(*, machine_ids, resource_id, context):
        _ = machine_ids, context
        if resource_id == "vmss-1":
            await asyncio.sleep(0.02)
        return {"provider_data": {"resource_id": resource_id}}

    handler.release_hosts_async = AsyncMock(side_effect=release_hosts_async)

    result = await _terminate_with_handler(
        handler=handler,
        instance_ids=["vm-1", "vm-2"],
        resource_mapping={"vm-1": ("vmss-1", 1), "vm-2": ("vmss-2", 1)},
        cleanup_recorder=cleanup_recorder,
    )

    assert result.success is True
    assert result.metadata["provider_data"]["termination_requests"] == [
        {"resource_id": "vmss-1"},
        {"resource_id": "vmss-2"},
    ]
    assert cleanup_recorder.call_args_list[0].args[0]["provider_data"]["resource_id"] == "vmss-1"
    assert cleanup_recorder.call_args_list[1].args[0]["provider_data"]["resource_id"] == "vmss-2"


@pytest.mark.asyncio
async def test_dispatch_reports_partial_failure_when_one_group_fails():
    cleanup_recorder = MagicMock()
    logger = MagicMock()
    handler = MagicMock()
    handler.release_hosts_async = AsyncMock(
        side_effect=[
            {"provider_data": {"resource_id": "vmss-1"}},
            RuntimeError("delete failed"),
        ]
    )

    result = await _terminate_with_handler(
        handler=handler,
        instance_ids=["vm-1", "vm-2"],
        resource_mapping={"vm-1": ("vmss-1", 1), "vm-2": ("vmss-2", 1)},
        logger=logger,
        cleanup_recorder=cleanup_recorder,
    )

    assert result.success is False
    assert result.error_code == "AZURE_TERMINATION_PARTIAL_FAILURE"
    provider_data = result.metadata["provider_data"]
    assert provider_data["termination_requests"] == [{"resource_id": "vmss-1"}]
    assert provider_data["successful_instance_ids"] == ["vm-1"]
    assert provider_data["dispatch_failures"] == [
        {
            "resource_id": "vmss-2",
            "instance_ids": ["vm-2"],
            "error_message": "delete failed",
            "error_type": "RuntimeError",
        }
    ]
    cleanup_recorder.assert_called_once()
    logger.warning.assert_called()


@pytest.mark.asyncio
async def test_dispatch_treats_success_without_provider_data_as_successful_group():
    handler = MagicMock()
    handler.release_hosts_async = AsyncMock(
        side_effect=[
            {},
            RuntimeError("delete failed"),
        ]
    )

    result = await _terminate_with_handler(
        handler=handler,
        instance_ids=["vm-1", "vm-2"],
        resource_mapping={"vm-1": ("vmss-1", 1), "vm-2": ("vmss-2", 1)},
    )

    assert result.success is False
    provider_data = result.metadata["provider_data"]
    assert "termination_requests" not in provider_data
    assert provider_data["successful_instance_ids"] == ["vm-1"]
    assert provider_data["failed_instance_ids"] == ["vm-2"]


@pytest.mark.asyncio
async def test_terminate_instances_async_reports_partial_dispatch_failure():
    cleanup_recorder = MagicMock()
    handler = MagicMock()
    handler.release_hosts_async = AsyncMock(
        side_effect=[
            {"provider_data": {"resource_id": "vmss-1"}},
            RuntimeError("delete failed"),
        ]
    )
    result = await _terminate_with_handler(
        handler=handler,
        instance_ids=["vm-1", "vm-2"],
        resource_mapping={"vm-1": ("vmss-1", 1), "vm-2": ("vmss-2", 1)},
        cleanup_recorder=cleanup_recorder,
    )

    assert result.success is False
    assert result.error_code == "AZURE_TERMINATION_PARTIAL_FAILURE"
    provider_data = result.metadata["provider_data"]
    assert provider_data["termination_requests"] == [{"resource_id": "vmss-1"}]
    assert provider_data["successful_instance_ids"] == ["vm-1"]
    assert provider_data["failed_instance_ids"] == ["vm-2"]
    assert provider_data["dispatch_failures"] == [
        {
            "resource_id": "vmss-2",
            "instance_ids": ["vm-2"],
            "error_message": "delete failed",
            "error_type": "RuntimeError",
        }
    ]


@pytest.mark.asyncio
async def test_dispatch_raises_when_all_groups_fail():
    handler = MagicMock()
    handler.release_hosts_async = AsyncMock(side_effect=RuntimeError("delete failed"))

    with pytest.raises(RuntimeError, match="delete failed"):
        await _terminate_with_handler(
            handler=handler,
            instance_ids=["vm-1"],
            resource_mapping={"vm-1": ("vmss-1", 1)},
        )


@pytest.mark.asyncio
async def test_execute_create_handler_async_normalizes_handler_result():
    service = AzureProvisioningService()
    handler = MagicMock()
    handler.acquire_hosts_async = AsyncMock(
        return_value={
            "success": True,
            "resource_ids": ["vm-1"],
            "instances": [],
            "error_message": None,
            "provider_data": {"operation_status": "submitted"},
        }
    )
    create_context = CreateOperationContext(
        template_config={"provider_api": "SingleVM"},
        count=1,
        provider_api=AzureProviderApi.SINGLE_VM,
        provider_api_key="SingleVM",
        handler=handler,
        azure_template=_make_template(),
    )

    result = await service.execute_create_handler_async(
        create_context=create_context,
        request=MagicMock(),
    )

    assert result.success is True
    assert result.data["resource_ids"] == ["vm-1"]
    assert result.metadata["handler_used"] == "SingleVM"


@pytest.mark.asyncio
async def test_execute_create_handler_async_returns_provider_error_for_failed_handler_result():
    service = AzureProvisioningService()
    handler = MagicMock()
    handler.acquire_hosts_async = AsyncMock(
        return_value={
            "success": False,
            "resource_ids": [],
            "instances": [],
            "error_message": "quota exhausted",
            "provider_data": {"error_codes": ["AllocationFailed"]},
        }
    )
    create_context = CreateOperationContext(
        template_config={"provider_api": "SingleVM"},
        count=1,
        provider_api=AzureProviderApi.SINGLE_VM,
        provider_api_key="SingleVM",
        handler=handler,
        azure_template=_make_template(),
    )

    result = await service.execute_create_handler_async(
        create_context=create_context,
        request=MagicMock(),
    )

    assert result.success is False
    assert result.error_code == "PROVISIONING_ADAPTER_ERROR"
    assert result.error_message == "Provisioning failed: quota exhausted"
    assert result.metadata["provider_data"]["error_codes"] == ["AllocationFailed"]


@pytest.mark.asyncio
async def test_get_instance_status_async_uses_async_handler_dispatch(resource_metadata_service):
    handler = MagicMock()
    handler.check_hosts_status_async = AsyncMock(
        return_value=[{"instance_id": "vm-1", "provider_data": {"vm_name": "vm-1"}}]
    )
    service = AzureInventoryService(
        logger=MagicMock(),
        provider_instance_name="azure-default",
        resource_metadata_service=resource_metadata_service,
        handler_provider=_handler_provider(handler),
        vmss_cleanup_coordinator=MagicMock(),
    )
    read_context = AzureReadOperationContext(
        operation_name="get_instance_status",
        request_id="req-12345678-1234-1234-1234-123456789012",
        template_id="tpl-1",
        request_metadata={},
        cyclecloud_request_context=MagicMock(),
        provider_api=AzureProviderApi.SINGLE_VM,
        provider_api_key="SingleVM",
        resource_group="test-rg",
        instance_ids=["vm-1"],
        resource_ids=["vm-1"],
        direct_resource_id="vm-1",
    )

    result = await service.get_instance_status_async(read_context)

    assert result.success is True
    assert result.data["instances"][0]["instance_id"] == "vm-1"
    handler.check_hosts_status_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_instance_status_async_rejects_queries_without_provider_api(
    resource_metadata_service,
):
    service = AzureInventoryService(
        logger=MagicMock(),
        provider_instance_name="azure-default",
        resource_metadata_service=resource_metadata_service,
        handler_provider=_handler_provider(None),
        vmss_cleanup_coordinator=MagicMock(),
    )
    read_context = AzureReadOperationContext(
        operation_name="get_instance_status",
        request_id="req-12345678-1234-1234-1234-123456789012",
        template_id="tpl-1",
        request_metadata={},
        cyclecloud_request_context=MagicMock(),
        provider_api=None,
        provider_api_key=None,
        resource_group="test-rg",
        instance_ids=["vm-1"],
    )
    with pytest.raises(AzureValidationError, match="provider_api-backed handler resolution"):
        await service.get_instance_status_async(read_context)


@pytest.mark.asyncio
async def test_describe_resource_instances_async_builds_result_from_async_handler(
    resource_metadata_service,
):
    handler = MagicMock()
    handler.check_hosts_status_async = AsyncMock(
        return_value=[{"instance_id": "vm-1", "provider_data": {"vm_name": "vm-1"}}]
    )
    service = AzureInventoryService(
        logger=MagicMock(),
        provider_instance_name="azure-default",
        resource_metadata_service=resource_metadata_service,
        handler_provider=_handler_provider(handler),
        vmss_cleanup_coordinator=MagicMock(),
    )
    read_context = AzureReadOperationContext(
        operation_name="describe_resource_instances",
        request_id="req-12345678-1234-1234-1234-123456789012",
        template_id="tpl-1",
        request_metadata={"deployment_name": "dep-1"},
        cyclecloud_request_context=MagicMock(),
        provider_api=AzureProviderApi.SINGLE_VM,
        provider_api_key="SingleVM",
        resource_group="test-rg",
        resource_ids=["vm-1"],
    )

    result = await service.describe_resource_instances_async(
        read_context=read_context,
        resource_manager=None,
        deployment_service=None,
    )

    assert result.success is True
    assert result.data["instances"][0]["instance_id"] == "vm-1"
    resource_metadata_service.augment_shortfall_metadata.assert_called_once()
    resource_metadata_service.augment_single_vm_deployment_metadata_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_template_deployment_async_uses_async_resources_api():
    azure_client = MagicMock()
    async_resource_client = MagicMock()
    async_resource_client.resources.begin_create_or_update = AsyncMock()
    azure_client.get_async_resource_client = AsyncMock(return_value=async_resource_client)
    service = AzureDeploymentService(azure_client=azure_client, logger=MagicMock())

    deployment_name = await service.submit_template_deployment_async(
        resource_group="test-rg",
        deployment_name="dep-1",
        template={"resources": []},
        parameters={"vmName": {"value": "vm-1"}},
    )

    assert deployment_name == "dep-1"
    async_resource_client.resources.begin_create_or_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_deployment_status_async_extracts_provisioning_and_error_fields():
    azure_client = MagicMock()
    async_resource_client = MagicMock()
    async_resource_client.resources.get = AsyncMock(
        return_value=SimpleNamespace(
            properties={
                "provisioningState": "Failed",
                "error": {
                    "code": "DeploymentFailed",
                    "message": "template validation failed",
                },
            }
        )
    )
    azure_client.get_async_resource_client = AsyncMock(return_value=async_resource_client)
    service = AzureDeploymentService(azure_client=azure_client, logger=MagicMock())

    status = await service.get_deployment_status_async(
        resource_group="test-rg",
        deployment_name="dep-1",
    )

    assert status == {
        "provisioning_state": "Failed",
        "error_code": "DeploymentFailed",
        "error_message": "template validation failed",
    }


@pytest.mark.asyncio
async def test_describe_resource_instances_async_awaits_vmss_async_metadata_and_cleanup(
    resource_metadata_service,
):
    handler = MagicMock()
    handler.check_hosts_status_async = AsyncMock(return_value=[])
    handler.get_vmss_resource_errors_async = AsyncMock(
        return_value=[{"error_code": "ProvisioningStateFailed"}]
    )
    cleanup = MagicMock()
    cleanup.reconcile = AsyncMock()
    service = AzureInventoryService(
        logger=MagicMock(),
        provider_instance_name="azure-default",
        resource_metadata_service=resource_metadata_service,
        handler_provider=_handler_provider(handler),
        vmss_cleanup_coordinator=cleanup,
    )
    read_context = AzureReadOperationContext(
        operation_name="describe_resource_instances",
        request_id="req-12345678-1234-1234-1234-123456789012",
        template_id="tpl-1",
        request_metadata={},
        cyclecloud_request_context=MagicMock(),
        provider_api=AzureProviderApi.VMSS,
        provider_api_key="VMSS",
        resource_group="test-rg",
        resource_ids=["vmss-1"],
    )

    result = await service.describe_resource_instances_async(
        read_context=read_context,
        resource_manager=MagicMock(),
        deployment_service=None,
    )

    assert result.success is True
    assert result.metadata["fleet_errors"] == [{"error_code": "ProvisioningStateFailed"}]
    handler.get_vmss_resource_errors_async.assert_awaited_once_with("test-rg", "vmss-1")
    resource_metadata_service.augment_vmss_capacity_metadata_async.assert_awaited_once()
    cleanup.reconcile.assert_awaited_once()


@pytest.mark.asyncio
async def test_describe_resource_instances_async_uses_empty_vmss_error_list_without_override(
    resource_metadata_service,
):
    handler = MagicMock()
    handler.check_hosts_status_async = AsyncMock(return_value=[])
    handler.get_vmss_resource_errors_async = AsyncMock(return_value=[])
    service = AzureInventoryService(
        logger=MagicMock(),
        provider_instance_name="azure-default",
        resource_metadata_service=resource_metadata_service,
        handler_provider=_handler_provider(handler),
        vmss_cleanup_coordinator=MagicMock(reconcile=AsyncMock()),
    )
    read_context = AzureReadOperationContext(
        operation_name="describe_resource_instances",
        request_id="req-12345678-1234-1234-1234-123456789012",
        template_id="tpl-1",
        request_metadata={},
        cyclecloud_request_context=MagicMock(),
        provider_api=AzureProviderApi.VMSS,
        provider_api_key="VMSS",
        resource_group="test-rg",
        resource_ids=["vmss-1"],
    )

    result = await service.describe_resource_instances_async(
        read_context=read_context,
        resource_manager=MagicMock(),
        deployment_service=None,
    )

    assert result.success is True
    assert "fleet_errors" not in result.metadata
    handler.get_vmss_resource_errors_async.assert_awaited_once_with("test-rg", "vmss-1")


@pytest.mark.asyncio
async def test_describe_resource_instances_async_warns_when_vmss_handler_lacks_error_reader(
    resource_metadata_service,
):
    logger = MagicMock()
    handler = MagicMock(spec=["check_hosts_status_async"])
    handler.check_hosts_status_async = AsyncMock(return_value=[])
    service = AzureInventoryService(
        logger=logger,
        provider_instance_name="azure-default",
        resource_metadata_service=resource_metadata_service,
        handler_provider=_handler_provider(handler),
        vmss_cleanup_coordinator=MagicMock(reconcile=AsyncMock()),
    )
    read_context = AzureReadOperationContext(
        operation_name="describe_resource_instances",
        request_id="req-12345678-1234-1234-1234-123456789012",
        template_id="tpl-1",
        request_metadata={},
        cyclecloud_request_context=MagicMock(),
        provider_api=AzureProviderApi.VMSS,
        provider_api_key="VMSS",
        resource_group="test-rg",
        resource_ids=["vmss-1"],
    )

    result = await service.describe_resource_instances_async(
        read_context=read_context,
        resource_manager=MagicMock(),
        deployment_service=None,
    )

    assert result.success is True
    assert "fleet_errors" not in result.metadata
    logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_health_check_async_uses_short_lived_async_token_provider(monkeypatch):
    config = MagicMock()
    config.region = "eastus2"
    config.client_id = "client-id"
    service = AzureHealthCheckService(config=config, logger=MagicMock())
    provider = MagicMock()
    provider.get_access_token = AsyncMock(return_value="token")
    provider_cls = MagicMock(return_value=provider)
    monkeypatch.setattr(
        "orb.providers.azure.services.health_check_service.AsyncDefaultAzureAccessTokenProvider",
        provider_cls,
    )

    result = await service.check_health_async()

    assert result.is_healthy is True
    provider_cls.assert_called_once_with(client_id="client-id", logger=service._logger)
    provider.get_access_token.assert_awaited_once_with("https://management.azure.com/.default")


def test_health_check_sync_uses_short_lived_sync_token_provider(monkeypatch):
    config = MagicMock()
    config.region = "eastus2"
    config.client_id = "client-id"
    service = AzureHealthCheckService(config=config, logger=MagicMock())
    provider = MagicMock()
    provider.get_access_token.return_value = "token"
    provider_cls = MagicMock(return_value=provider)
    monkeypatch.setattr(
        "orb.providers.azure.services.health_check_service.DefaultAzureAccessTokenProvider",
        provider_cls,
    )

    result = service.check_health()

    assert result.is_healthy is True
    provider_cls.assert_called_once_with(client_id="client-id", logger=service._logger)
    provider.get_access_token.assert_called_once_with("https://management.azure.com/.default")
