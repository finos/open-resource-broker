"""Focused tests for AzureResourceManager."""

from unittest.mock import MagicMock

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


def test_tag_resource_submits_requested_tags():
    manager, azure_client, _ = _make_manager()

    manager.tag_resource(
        "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm-1",
        {"env": "test"},
    )

    azure_client.resource_client.tags.begin_create_or_update_at_scope.assert_called_once_with(
        scope="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm-1",
        parameters={"properties": {"tags": {"env": "test"}}},
    )


def test_get_vmss_capacity_uses_vmss_capacity_and_member_count():
    manager, azure_client, _ = _make_manager()
    vmss = MagicMock()
    vmss.sku.capacity = 5
    vmss.sku.name = "Standard_D4s_v5"
    vmss.orchestration_mode = "Flexible"
    vmss.provisioning_state = "Updating"
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss
    azure_client.compute_client.virtual_machines.list.return_value = [
        MagicMock(virtual_machine_scale_set=MagicMock(id="/.../virtualMachineScaleSets/vmss-1")),
        MagicMock(virtual_machine_scale_set=MagicMock(id="/.../virtualMachineScaleSets/vmss-1")),
    ]

    result = manager.get_vmss_capacity("test-rg", "vmss-1")

    assert result == {
        "vmss_name": "vmss-1",
        "resource_group": "test-rg",
        "capacity": 5,
        "vm_size": "Standard_D4s_v5",
        "provisioning_state": "Updating",
        "provisioned_instance_count": 2,
    }


def test_get_vmss_capacity_raises_infrastructure_error_when_lookup_fails():
    manager, azure_client, _ = _make_manager()
    azure_client.compute_client.virtual_machine_scale_sets.get.side_effect = RuntimeError("boom")

    with pytest.raises(AzureInfrastructureError, match="Failed to get VMSS capacity"):
        manager.get_vmss_capacity("test-rg", "vmss-1")


def test_get_vmss_member_count_counts_uniform_instances():
    manager, azure_client, _ = _make_manager()
    azure_client.compute_client.virtual_machine_scale_set_vms.list.return_value = [
        MagicMock(),
        MagicMock(),
        MagicMock(),
    ]

    assert manager.get_vmss_member_count("test-rg", "vmss-1", orchestration_mode="Uniform") == 3


def test_get_vmss_member_count_tolerates_count_errors_and_returns_none():
    manager, azure_client, logger = _make_manager()
    azure_client.compute_client.virtual_machine_scale_set_vms.list.side_effect = RuntimeError("boom")

    assert manager.get_vmss_member_count("test-rg", "vmss-1", orchestration_mode="Uniform") is None
    logger.warning.assert_called_once()


def test_vmss_exists_recognizes_not_found_errors():
    manager, azure_client, _ = _make_manager()
    exc = RuntimeError("not found")
    exc.status_code = 404
    azure_client.compute_client.virtual_machine_scale_sets.get.side_effect = exc

    assert manager.vmss_exists("test-rg", "vmss-1") is False


def test_vmss_exists_returns_none_for_unknown_lookup_error():
    manager, azure_client, logger = _make_manager()
    azure_client.compute_client.virtual_machine_scale_sets.get.side_effect = RuntimeError("boom")

    assert manager.vmss_exists("test-rg", "vmss-1") is None
    logger.warning.assert_called_once()


def test_scale_vmss_updates_capacity_and_submits_create_or_update():
    manager, azure_client, _ = _make_manager()
    vmss = MagicMock()
    vmss.sku.capacity = 1
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss

    manager.scale_vmss("test-rg", "vmss-1", 4)

    assert vmss.sku.capacity == 4
    azure_client.compute_client.virtual_machine_scale_sets.begin_create_or_update.assert_called_once_with(
        resource_group_name="test-rg",
        vm_scale_set_name="vmss-1",
        parameters=vmss,
    )


def test_get_compute_usage_uses_config_region_by_default():
    manager, azure_client, _ = _make_manager()
    usage = MagicMock()
    usage.name.value = "standardDSv5Family"
    usage.current_value = 2
    usage.limit = 10
    usage.unit = "Count"
    azure_client.compute_client.usage.list.return_value = [usage]

    result = manager.get_compute_usage()

    assert result == [
        {
            "name": "standardDSv5Family",
            "current_value": 2,
            "limit": 10,
            "unit": "Count",
        }
    ]
    azure_client.compute_client.usage.list.assert_called_once_with(location="eastus2")
