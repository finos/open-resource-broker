"""Focused tests for AzureResourceManager."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.exceptions.azure_exceptions import AzureInfrastructureError
from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager


def _make_manager():
    azure_client = MagicMock()
    logger = MagicMock()
    config = AzureProviderConfig(
        subscription_id="12345678-1234-1234-1234-123456789012",
        resource_group="test-rg",
        region="eastus2",
    )
    return (
        AzureResourceManager(azure_client=azure_client, config=config, logger=logger),
        azure_client,
        logger,
    )


@pytest.mark.asyncio
async def test_get_vmss_capacity_async_uses_async_vmss_capacity_and_member_count():
    manager, azure_client, _ = _make_manager()
    async_compute_client = MagicMock()
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute_client)

    vmss = MagicMock()
    vmss.sku.capacity = 5
    vmss.sku.name = "Standard_D4s_v5"
    vmss.orchestration_mode = "Flexible"
    vmss.provisioning_state = "Updating"
    async_compute_client.virtual_machine_scale_sets.get = AsyncMock(return_value=vmss)

    async def _vm_iter():
        yield MagicMock(
            virtual_machine_scale_set=MagicMock(id="/.../virtualMachineScaleSets/vmss-1")
        )
        yield MagicMock(
            virtual_machine_scale_set=MagicMock(id="/.../virtualMachineScaleSets/vmss-1")
        )

    async_compute_client.virtual_machines.list.return_value = _vm_iter()

    result = await manager.get_vmss_capacity_async("test-rg", "vmss-1")

    assert result == {
        "vmss_name": "vmss-1",
        "resource_group": "test-rg",
        "capacity": 5,
        "vm_size": "Standard_D4s_v5",
        "provisioning_state": "Updating",
        "provisioned_instance_count": 2,
    }


@pytest.mark.asyncio
async def test_get_vmss_capacity_async_raises_infrastructure_error_when_lookup_fails():
    manager, azure_client, _ = _make_manager()
    async_compute_client = MagicMock()
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute_client)
    async_compute_client.virtual_machine_scale_sets.get = AsyncMock(
        side_effect=RuntimeError("boom")
    )

    with pytest.raises(AzureInfrastructureError, match="Failed to get VMSS capacity"):
        await manager.get_vmss_capacity_async("test-rg", "vmss-1")


@pytest.mark.asyncio
async def test_get_vmss_member_count_async_counts_uniform_instances():
    manager, azure_client, _ = _make_manager()
    async_compute_client = MagicMock()
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute_client)

    async def _vm_iter():
        yield MagicMock()
        yield MagicMock()
        yield MagicMock()

    async_compute_client.virtual_machine_scale_set_vms.list.return_value = _vm_iter()

    assert (
        await manager.get_vmss_member_count_async("test-rg", "vmss-1", orchestration_mode="Uniform")
        == 3
    )


@pytest.mark.asyncio
async def test_get_vmss_member_count_async_tolerates_count_errors_and_returns_none():
    manager, azure_client, logger = _make_manager()
    async_compute_client = MagicMock()
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute_client)
    async_compute_client.virtual_machine_scale_set_vms.list.side_effect = RuntimeError("boom")

    assert (
        await manager.get_vmss_member_count_async("test-rg", "vmss-1", orchestration_mode="Uniform")
        is None
    )
    logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_vmss_exists_async_recognizes_not_found_errors():
    manager, azure_client, _ = _make_manager()
    async_compute_client = MagicMock()
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute_client)
    exc = RuntimeError("not found")
    exc.status_code = 404
    async_compute_client.virtual_machine_scale_sets.get = AsyncMock(side_effect=exc)

    assert await manager.vmss_exists_async("test-rg", "vmss-1") is False


@pytest.mark.asyncio
async def test_vmss_exists_async_returns_none_for_unknown_lookup_error():
    manager, azure_client, logger = _make_manager()
    async_compute_client = MagicMock()
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute_client)
    async_compute_client.virtual_machine_scale_sets.get = AsyncMock(
        side_effect=RuntimeError("boom")
    )

    assert await manager.vmss_exists_async("test-rg", "vmss-1") is None
    logger.warning.assert_called_once()
