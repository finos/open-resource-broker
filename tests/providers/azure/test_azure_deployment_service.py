"""Focused tests for Azure deployment helpers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.azure.infrastructure.services.azure_deployment_service import (
    AzureDeploymentService,
)


def test_build_deployment_name_normalizes_and_truncates_deterministically():
    parts = (
        "vm",
        "req with spaces",
        "template/with/slashes",
        "x" * 80,
    )
    name = AzureDeploymentService.build_deployment_name(*parts)
    distinct_name = AzureDeploymentService.build_deployment_name(*parts[:-1], f"{'x' * 79}y")

    assert len(name) == 64
    assert " " not in name
    assert "/" not in name
    assert name.startswith("vm-req-with-spaces-template-with-slashes-")
    assert AzureDeploymentService.build_deployment_name(*parts) == name
    assert distinct_name != name


def test_resource_id_expression_targets_sibling_resource():
    assert (
        AzureDeploymentService.resource_id_expression(
            "Microsoft.Network/networkInterfaces",
            "nic-vm-1",
        )
        == "[resourceId('Microsoft.Network/networkInterfaces', 'nic-vm-1')]"
    )


@pytest.mark.asyncio
async def test_submit_template_deployment_async_uses_resources_api_without_waiting():
    azure_client = MagicMock()
    async_resource_client = MagicMock()
    async_resource_client.resources.begin_create_or_update = AsyncMock()
    azure_client.get_async_resource_client = AsyncMock(return_value=async_resource_client)
    logger = MagicMock()
    service = AzureDeploymentService(azure_client=azure_client, logger=logger)
    template = {"resources": []}

    deployment_name = await service.submit_template_deployment_async(
        resource_group="test-rg",
        deployment_name="dep-1",
        template=template,
        parameters={"vmName": {"value": "vm-1"}},
    )

    assert deployment_name == "dep-1"
    async_resource_client.resources.begin_create_or_update.assert_awaited_once_with(
        resource_group_name="test-rg",
        resource_provider_namespace="Microsoft.Resources",
        parent_resource_path="",
        resource_type="deployments",
        resource_name="dep-1",
        api_version="2025-04-01",
        parameters={
            "properties": {
                "mode": "Incremental",
                "template": template,
                "parameters": {"vmName": {"value": "vm-1"}},
            }
        },
    )


@pytest.mark.asyncio
async def test_get_deployment_status_async_extracts_provisioning_and_error_fields():
    azure_client = MagicMock()
    async_resource_client = MagicMock()
    async_resource_client.resources.get = AsyncMock()
    azure_client.get_async_resource_client = AsyncMock(return_value=async_resource_client)
    logger = MagicMock()
    service = AzureDeploymentService(azure_client=azure_client, logger=logger)
    deployment = MagicMock()
    deployment.properties = {
        "provisioningState": "Failed",
        "error": {
            "code": "DeploymentFailed",
            "message": "template validation failed",
        },
    }
    async_resource_client.resources.get.return_value = deployment

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
async def test_get_deployment_status_async_tolerates_non_mapping_properties():
    azure_client = MagicMock()
    async_resource_client = MagicMock()
    async_resource_client.resources.get = AsyncMock()
    azure_client.get_async_resource_client = AsyncMock(return_value=async_resource_client)
    logger = MagicMock()
    service = AzureDeploymentService(azure_client=azure_client, logger=logger)
    deployment = MagicMock()
    deployment.properties = None
    async_resource_client.resources.get.return_value = deployment

    status = await service.get_deployment_status_async(
        resource_group="test-rg",
        deployment_name="dep-1",
    )

    assert status == {
        "provisioning_state": None,
        "error_code": None,
        "error_message": None,
    }


@pytest.mark.asyncio
async def test_cleanup_failed_single_vm_deployment_deletes_in_reverse_dependency_order():
    azure_client = MagicMock()
    async_resource_client = MagicMock()
    delete_pollers = [MagicMock(), MagicMock(), MagicMock()]
    for poller in delete_pollers:
        poller.result = AsyncMock()
    async_resource_client.resources.begin_delete = AsyncMock(side_effect=delete_pollers)
    azure_client.get_async_resource_client = AsyncMock(return_value=async_resource_client)
    service = AzureDeploymentService(azure_client=azure_client, logger=MagicMock())

    result = await service.cleanup_failed_single_vm_deployment_async(
        resource_group="test-rg",
        resources=[
            {
                "vm_name": "vm-test",
                "nic_name": "nic-vm-test",
                "public_ip_name": "pip-vm-test",
            }
        ],
    )

    assert result == {
        "status": "succeeded",
        "completed_resources": [
            "Microsoft.Compute/virtualMachines/vm-test",
            "Microsoft.Network/networkInterfaces/nic-vm-test",
            "Microsoft.Network/publicIPAddresses/pip-vm-test",
        ],
        "failed_resources": [],
    }
    assert [
        call.kwargs["resource_type"]
        for call in async_resource_client.resources.begin_delete.await_args_list
    ] == ["virtualMachines", "networkInterfaces", "publicIPAddresses"]
    for poller in delete_pollers:
        poller.result.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_failed_single_vm_deployment_reports_partial_cleanup():
    azure_client = MagicMock()
    async_resource_client = MagicMock()
    vm_poller = MagicMock()
    vm_poller.result = AsyncMock(side_effect=RuntimeError("VM delete failed"))
    nic_poller = MagicMock()
    nic_poller.result = AsyncMock()
    async_resource_client.resources.begin_delete = AsyncMock(side_effect=[vm_poller, nic_poller])
    azure_client.get_async_resource_client = AsyncMock(return_value=async_resource_client)
    service = AzureDeploymentService(azure_client=azure_client, logger=MagicMock())

    result = await service.cleanup_failed_single_vm_deployment_async(
        resource_group="test-rg",
        resources=[
            {
                "vm_name": "vm-test",
                "nic_name": "nic-vm-test",
                "public_ip_name": None,
            }
        ],
    )

    assert result == {
        "status": "partial",
        "completed_resources": ["Microsoft.Network/networkInterfaces/nic-vm-test"],
        "failed_resources": [
            {
                "resource": "Microsoft.Compute/virtualMachines/vm-test",
                "error": "VM delete failed",
            }
        ],
    }


def test_build_single_vm_deployment_template_attaches_public_ip_when_enabled():
    service = AzureDeploymentService(azure_client=MagicMock(), logger=MagicMock())
    deployment_template = service.build_single_vm_deployment_template(
        location="eastus2",
        subnet_id="/subscriptions/.../subnets/default",
        vm_definitions=[
            {
                "vm_name": "vm-test",
                "nic_name": "nic-vm-test",
                "public_ip_name": "pip-vm-test",
                "vm_payload": {
                    "location": "eastus2",
                    "properties": {
                        "networkProfile": {
                            "networkInterfaces": [
                                {
                                    "id": service.resource_id_expression(
                                        "Microsoft.Network/networkInterfaces",
                                        "nic-vm-test",
                                    ),
                                    "properties": {
                                        "primary": True,
                                        "deleteOption": "Delete",
                                    },
                                }
                            ]
                        },
                        "storageProfile": {
                            "osDisk": {"deleteOption": "Delete"},
                            "dataDisks": [{"deleteOption": "Delete"}],
                        },
                    },
                },
            }
        ],
    )

    public_ip_resource = next(
        resource
        for resource in deployment_template["resources"]
        if resource["type"] == "Microsoft.Network/publicIPAddresses"
    )
    nic_resource = next(
        resource
        for resource in deployment_template["resources"]
        if resource["type"] == "Microsoft.Network/networkInterfaces"
    )
    vm_resource = next(
        resource
        for resource in deployment_template["resources"]
        if resource["type"] == "Microsoft.Compute/virtualMachines"
    )

    assert public_ip_resource == {
        "type": "Microsoft.Network/publicIPAddresses",
        "apiVersion": "2023-09-01",
        "name": "pip-vm-test",
        "location": "eastus2",
        "sku": {"name": "Standard"},
        "properties": {
            "publicIPAllocationMethod": "Static",
            "deleteOption": "Delete",
        },
    }
    assert nic_resource["properties"]["ipConfigurations"][0]["properties"]["publicIPAddress"] == {
        "id": "[resourceId('Microsoft.Network/publicIPAddresses', 'pip-vm-test')]",
        "deleteOption": "Delete",
    }
    assert vm_resource["properties"]["storageProfile"]["osDisk"]["deleteOption"] == "Delete"
    assert vm_resource["properties"]["storageProfile"]["dataDisks"][0]["deleteOption"] == "Delete"
