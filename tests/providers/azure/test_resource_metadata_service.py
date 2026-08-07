"""Focused tests for Azure resource metadata enrichment."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.azure.services.resource_metadata_service import (
    AzureResourceMetadataService,
)


@pytest.mark.asyncio
async def test_augment_vmss_capacity_metadata_async_collects_multiple_vmss_concurrently():
    service = AzureResourceMetadataService(
        default_resource_group="test-rg",
        logger=MagicMock(),
    )
    resource_manager = MagicMock()

    async def get_capacity(resource_group: str, vmss_name: str) -> dict[str, object]:
        _ = resource_group
        if vmss_name == "vmss-a":
            await asyncio.sleep(0.02)
            return {
                "capacity": 2,
                "provisioned_instance_count": 1,
                "provisioning_state": "Updating",
            }
        await asyncio.sleep(0.0)
        return {
            "capacity": 3,
            "provisioned_instance_count": 3,
            "provisioning_state": "Succeeded",
        }

    resource_manager.get_vmss_capacity_async = AsyncMock(side_effect=get_capacity)
    metadata: dict[str, object] = {}

    await service.augment_vmss_capacity_metadata_async(
        metadata,
        ["vmss-a", "vmss-b"],
        resource_manager=resource_manager,
    )

    assert metadata["fleet_capacity_fulfilment"] == {
        "target_capacity_units": 5,
        "fulfilled_capacity_units": 4,
        "provisioned_instance_count": 4,
        "state": "multiple",
    }
    assert metadata["fleet_capacity_fulfilment_by_resource"] == {
        "vmss-a": {
            "target_capacity_units": 2,
            "fulfilled_capacity_units": 1,
            "provisioned_instance_count": 1,
            "state": "Updating",
        },
        "vmss-b": {
            "target_capacity_units": 3,
            "fulfilled_capacity_units": 3,
            "provisioned_instance_count": 3,
            "state": "Succeeded",
        },
    }


@pytest.mark.asyncio
async def test_augment_vmss_capacity_metadata_async_skips_failed_snapshot_and_preserves_order():
    logger = MagicMock()
    service = AzureResourceMetadataService(
        default_resource_group="test-rg",
        logger=logger,
    )
    resource_manager = MagicMock()

    async def get_capacity(resource_group: str, vmss_name: str) -> dict[str, object]:
        _ = resource_group
        if vmss_name == "vmss-a":
            raise RuntimeError("boom")
        return {
            "capacity": 1,
            "provisioned_instance_count": 1,
            "provisioning_state": "Succeeded",
        }

    resource_manager.get_vmss_capacity_async = AsyncMock(side_effect=get_capacity)
    metadata: dict[str, object] = {}

    await service.augment_vmss_capacity_metadata_async(
        metadata,
        ["vmss-a", "vmss-b"],
        resource_manager=resource_manager,
    )

    assert metadata["fleet_capacity_fulfilment"] == {
        "target_capacity_units": 1,
        "fulfilled_capacity_units": 1,
        "provisioned_instance_count": 1,
        "state": "Succeeded",
    }
    assert "fleet_capacity_fulfilment_by_resource" not in metadata
    logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_failed_single_vm_deployment_cleans_submitted_resources():
    service = AzureResourceMetadataService(
        default_resource_group="test-rg",
        logger=MagicMock(),
    )
    deployment_service = MagicMock()
    deployment_service.get_deployment_status_async = AsyncMock(
        return_value={
            "provisioning_state": "Failed",
            "error_code": "DeploymentFailed",
            "error_message": "VM provisioning failed",
        }
    )
    cleanup_result = {
        "status": "succeeded",
        "completed_resources": [
            "Microsoft.Compute/virtualMachines/vm-test",
            "Microsoft.Network/networkInterfaces/nic-vm-test",
            "Microsoft.Network/publicIPAddresses/pip-vm-test",
        ],
        "failed_resources": [],
    }
    deployment_service.cleanup_failed_single_vm_deployment_async = AsyncMock(
        return_value=cleanup_result
    )
    metadata: dict[str, object] = {}

    await service.augment_single_vm_deployment_metadata_async(
        metadata,
        {
            "deployment_name": "dep-test",
            "submitted_vms": [
                {
                    "vm_name": "vm-test",
                    "nic_name": "nic-vm-test",
                    "public_ip_name": "pip-vm-test",
                    "selected_vm_size": "Standard_D2s_v5",
                }
            ],
        },
        resource_group="test-rg",
        deployment_service=deployment_service,
    )

    deployment_service.cleanup_failed_single_vm_deployment_async.assert_awaited_once_with(
        resource_group="test-rg",
        resources=[
            {
                "vm_name": "vm-test",
                "nic_name": "nic-vm-test",
                "public_ip_name": "pip-vm-test",
            }
        ],
    )
    assert metadata["failed_deployment_cleanup"] == cleanup_result
    assert metadata["fleet_errors"] == [
        {
            "error_code": "DeploymentFailed",
            "error_message": "VM provisioning failed",
            "resource_group": "test-rg",
            "instance_id": "dep-test",
        }
    ]


@pytest.mark.asyncio
async def test_successful_single_vm_deployment_does_not_run_failed_cleanup():
    service = AzureResourceMetadataService(
        default_resource_group="test-rg",
        logger=MagicMock(),
    )
    deployment_service = MagicMock()
    deployment_service.get_deployment_status_async = AsyncMock(
        return_value={
            "provisioning_state": "Succeeded",
            "error_code": None,
            "error_message": None,
        }
    )
    deployment_service.cleanup_failed_single_vm_deployment_async = AsyncMock()
    metadata: dict[str, object] = {}

    await service.augment_single_vm_deployment_metadata_async(
        metadata,
        {
            "deployment_name": "dep-test",
            "submitted_vms": [
                {
                    "vm_name": "vm-test",
                    "nic_name": "nic-vm-test",
                    "public_ip_name": None,
                }
            ],
        },
        resource_group="test-rg",
        deployment_service=deployment_service,
    )

    deployment_service.cleanup_failed_single_vm_deployment_async.assert_not_awaited()
    assert "failed_deployment_cleanup" not in metadata


def test_attach_provider_fulfilment_uses_vmss_capacity_metadata():
    service = AzureResourceMetadataService(
        default_resource_group="test-rg",
        logger=MagicMock(),
    )
    metadata = {
        "fleet_capacity_fulfilment": {
            "target_capacity_units": 3,
            "fulfilled_capacity_units": 2,
            "provisioned_instance_count": 2,
            "state": "Updating",
        }
    }

    service.attach_provider_fulfilment(
        metadata,
        instances=[],
        target_units=None,
    )

    fulfilment = metadata["provider_fulfilment"]
    assert fulfilment.state == "in_progress"
    assert fulfilment.target_units == 3
    assert fulfilment.fulfilled_units == 2


def test_attach_provider_fulfilment_reports_failed_single_vm_deployment():
    service = AzureResourceMetadataService(
        default_resource_group="test-rg",
        logger=MagicMock(),
    )
    metadata = {
        "fleet_errors": [
            {
                "error_code": "OperationNotAllowed",
                "error_message": "quota exceeded",
            }
        ]
    }

    service.attach_provider_fulfilment(
        metadata,
        instances=[],
        target_units=1,
    )

    fulfilment = metadata["provider_fulfilment"]
    assert fulfilment.state == "failed"
    assert fulfilment.target_units == 1
    assert fulfilment.fulfilled_units == 0
