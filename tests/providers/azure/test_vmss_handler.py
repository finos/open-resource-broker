"""Focused tests for VMSS handler behavior."""

import pytest
from unittest.mock import MagicMock

from orb.providers.azure.domain.template.value_objects import AzureVMSSOrchestrationMode
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.infrastructure.handlers.single_vm_handler import SingleVMHandler
from orb.providers.azure.infrastructure.handlers.vmss_handler import VMSSHandler
from orb.providers.azure.infrastructure.services.azure_deployment_service import (
    AzureDeploymentService,
)


def _make_template(**overrides) -> AzureTemplate:
    config = {
        "template_id": "azure-vmss-test",
        "provider_api": "VMSS",
        "vm_size": "Standard_D4s_v5",
        "resource_group": "test-rg",
        "location": "eastus2",
        "network_config": {"subnet_id": "/subscriptions/.../subnets/default"},
        "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
        "image": {
            "publisher": "Canonical",
            "offer": "0001-com-ubuntu-server-jammy",
            "sku": "22_04-lts-gen2",
            "version": "latest",
        },
    }
    config.update(overrides)
    return AzureTemplate(**config)


def _deleted_vm_names(azure_client: MagicMock) -> list[str]:
    return [
        str(call.kwargs["vm_name"])
        for call in azure_client.compute_client.virtual_machines.begin_delete.call_args_list
    ]


def test_acquire_hosts_submits_native_vmss_create_and_returns_submitted_status():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    azure_client.compute_client.virtual_machine_scale_sets.begin_create_or_update.return_value = (
        MagicMock()
    )

    request = MagicMock()
    request.requested_count = 2
    request.request_id = "req-1"
    request.metadata = {}

    result = handler.acquire_hosts(request, _make_template())

    assert result["success"] is True
    assert result["resource_ids"] == [result["provider_data"]["vmss_name"]]
    assert result["instances"] == []
    assert result["provider_data"]["provisioning_state"] == "creating"
    assert result["provider_data"]["operation_status"] == "submitted"
    assert result["provider_data"]["fulfillment_final"] is True
    create_call = azure_client.compute_client.virtual_machine_scale_sets.begin_create_or_update.call_args.kwargs
    assert create_call["resource_group_name"] == "test-rg"
    assert create_call["vm_scale_set_name"] == result["provider_data"]["vmss_name"]
    assert create_call["parameters"]["name"] == result["provider_data"]["vmss_name"]
    assert create_call["parameters"]["sku"]["capacity"] == 2


def test_flexible_vmss_status_returns_only_member_vms():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.FLEXIBLE.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss

    member_vm = MagicMock()
    member_vm.name = "vmss-azure-test_abcd1234"
    member_vm.virtual_machine_scale_set.id = (
        "/subscriptions/sub/resourceGroups/test-rg/providers/"
        "Microsoft.Compute/virtualMachineScaleSets/vmss-azure-test"
    )
    member_vm.instance_view.statuses = []
    member_vm.hardware_profile.vm_size = "Standard_D4s_v5"
    member_vm.location = "eastus2"
    member_vm.zones = ["1"]

    member_vm_with_view = MagicMock()
    member_vm_with_view.name = "vmss-azure-test_abcd1234"
    member_vm_with_view.instance_id = "vmss-azure-test_abcd1234"
    member_vm_with_view.vm_id = "vm-guid-1"
    member_vm_with_view.instance_view.statuses = []
    member_vm_with_view.hardware_profile.vm_size = "Standard_D4s_v5"
    member_vm_with_view.location = "eastus2"
    member_vm_with_view.zones = ["1"]

    other_vm = MagicMock()
    other_vm.name = "other-vm"
    other_vm.virtual_machine_scale_set = None

    azure_client.compute_client.virtual_machines.list.return_value = [member_vm, other_vm]
    azure_client.compute_client.virtual_machines.get.return_value = member_vm_with_view

    request = MagicMock()
    request.resource_ids = ["vmss-azure-test"]
    request.metadata = {"resource_group": "test-rg"}

    result = handler.check_hosts_status(request)

    assert len(result) == 1
    assert result[0]["instance_id"] == "vmss-azure-test_abcd1234"


def test_flexible_vmss_status_uses_name_prefix_when_vmss_reference_is_missing():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.FLEXIBLE.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss

    member_vm = MagicMock()
    member_vm.name = "vmss-azure-test_abcd1234"
    member_vm.virtual_machine_scale_set = None
    member_vm.instance_view.statuses = []
    member_vm.hardware_profile.vm_size = "Standard_D4s_v5"
    member_vm.location = "eastus2"
    member_vm.zones = ["1"]

    member_vm_with_view = MagicMock()
    member_vm_with_view.name = "vmss-azure-test_abcd1234"
    member_vm_with_view.instance_id = "vmss-azure-test_abcd1234"
    member_vm_with_view.vm_id = "vm-guid-1"
    member_vm_with_view.instance_view.statuses = []
    member_vm_with_view.hardware_profile.vm_size = "Standard_D4s_v5"
    member_vm_with_view.location = "eastus2"
    member_vm_with_view.zones = ["1"]

    other_vm = MagicMock()
    other_vm.name = "other-vm"
    other_vm.virtual_machine_scale_set = None

    azure_client.compute_client.virtual_machines.list.return_value = [member_vm, other_vm]
    azure_client.compute_client.virtual_machines.get.return_value = member_vm_with_view

    request = MagicMock()
    request.resource_ids = ["vmss-azure-test"]
    request.metadata = {"resource_group": "test-rg"}

    result = handler.check_hosts_status(request)

    assert len(result) == 1
    assert result[0]["instance_id"] == "vmss-azure-test_abcd1234"


def test_single_vm_acquire_hosts_submits_one_batched_deployment_and_returns_submitted_status():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    azure_client.resource_client.resources.begin_create_or_update.return_value = MagicMock()

    request = MagicMock()
    request.requested_count = 2
    request.request_id = "req-2"
    request.metadata = {}

    template = AzureTemplate(
        template_id="azure-singlevm-test",
        provider_api="SingleVM",
        vm_size="Standard_D4s_v5",
        resource_group="test-rg",
        location="eastus2",
        network_config={"subnet_id": "/subscriptions/.../subnets/default"},
        ssh_public_keys=["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
        image={
            "publisher": "Canonical",
            "offer": "0001-com-ubuntu-server-jammy",
            "sku": "22_04-lts-gen2",
            "version": "latest",
        },
    )

    result = handler.acquire_hosts(request, template)

    assert result["success"] is True
    assert len(result["resource_ids"]) == 2
    assert result["instances"] == []
    assert result["provider_data"]["operation_status"] == "submitted"
    assert result["provider_data"]["fulfillment_final"] is True
    deployment_call = azure_client.resource_client.resources.begin_create_or_update.call_args.kwargs
    deployment_template = deployment_call[
        "parameters"
    ]["properties"]["template"]
    resource_types = [resource["type"] for resource in deployment_template["resources"]]
    assert resource_types.count("Microsoft.Network/networkInterfaces") == 2
    assert resource_types.count("Microsoft.Compute/virtualMachines") == 2
    assert result["provider_data"]["deployment_name"] == deployment_call["resource_name"]
    assert len(result["provider_data"]["submitted_vms"]) == 2


def test_single_vm_acquire_hosts_creates_public_ips_when_enabled():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    azure_client.resource_client.resources.begin_create_or_update.return_value = MagicMock()

    request = MagicMock()
    request.requested_count = 2
    request.request_id = "req-pip"
    request.metadata = {}

    template = AzureTemplate(
        template_id="azure-singlevm-test",
        provider_api="SingleVM",
        vm_size="Standard_D4s_v5",
        resource_group="test-rg",
        location="eastus2",
        network_config={
            "subnet_id": "/subscriptions/.../subnets/default",
            "public_ip_enabled": True,
        },
        ssh_public_keys=["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
        image={
            "publisher": "Canonical",
            "offer": "0001-com-ubuntu-server-jammy",
            "sku": "22_04-lts-gen2",
            "version": "latest",
        },
    )

    result = handler.acquire_hosts(request, template)

    assert result["success"] is True
    deployment_template = azure_client.resource_client.resources.begin_create_or_update.call_args.kwargs[
        "parameters"
    ]["properties"]["template"]
    public_ip_resources = [
        resource
        for resource in deployment_template["resources"]
        if resource["type"] == "Microsoft.Network/publicIPAddresses"
    ]
    nic_resources = [
        resource
        for resource in deployment_template["resources"]
        if resource["type"] == "Microsoft.Network/networkInterfaces"
    ]
    assert len(public_ip_resources) == 2
    assert len(nic_resources) == 2
    public_ip_ref = nic_resources[0]["properties"]["ipConfigurations"][0]["properties"]["publicIPAddress"]
    assert public_ip_resources[0]["name"].startswith("pip-vm-")
    assert "Microsoft.Network/publicIPAddresses" in public_ip_ref["id"]
    assert public_ip_ref["deleteOption"] == "Delete"


def test_single_vm_falls_back_to_alternate_vm_size_for_the_whole_batch():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    first_failure = Exception("primary size unavailable")
    first_failure.error_code = "AllocationFailed"
    azure_client.resource_client.resources.begin_create_or_update.side_effect = [
        first_failure,
        MagicMock(),
    ]

    request = MagicMock()
    request.requested_count = 2
    request.request_id = "req-4"
    request.metadata = {}

    template = AzureTemplate(
        template_id="azure-singlevm-test",
        provider_api="SingleVM",
        vm_size="Standard_D4s_v5",
        vm_sizes=["Standard_D8s_v5"],
        resource_group="test-rg",
        location="eastus2",
        network_config={"subnet_id": "/subscriptions/.../subnets/default"},
        ssh_public_keys=["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
        image={
            "publisher": "Canonical",
            "offer": "0001-com-ubuntu-server-jammy",
            "sku": "22_04-lts-gen2",
            "version": "latest",
        },
    )

    result = handler.acquire_hosts(request, template)

    assert result["success"] is True
    assert result["provider_data"]["submitted_count"] == 2
    assert all(
        submitted_vm["selected_vm_size"] == "Standard_D8s_v5"
        for submitted_vm in result["provider_data"]["submitted_vms"]
    )


def test_single_vm_build_vm_params_applies_disk_encryption_set_to_os_and_data_disks():
    template = AzureTemplate(
        template_id="azure-singlevm-test",
        provider_api="SingleVM",
        vm_size="Standard_D4s_v5",
        resource_group="test-rg",
        location="eastus2",
        network_config={"subnet_id": "/subscriptions/.../subnets/default"},
        ssh_public_keys=["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
        image={
            "publisher": "Canonical",
            "offer": "0001-com-ubuntu-server-jammy",
            "sku": "22_04-lts-gen2",
            "version": "latest",
        },
        disk_encryption_set_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/diskEncryptionSets/des-1",
        data_disks=[{"lun": 0, "disk_size_gb": 128}],
    )

    params = SingleVMHandler._build_vm_params(
        template=template,
        vm_name="vm-test",
        nic_id="/subscriptions/.../networkInterfaces/nic-vm-test",
    )
    storage_profile = params["properties"]["storageProfile"]

    assert storage_profile["osDisk"]["managedDisk"]["diskEncryptionSet"] == {
        "id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/diskEncryptionSets/des-1"
    }
    assert storage_profile["dataDisks"][0]["managedDisk"]["diskEncryptionSet"] == {
        "id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/diskEncryptionSets/des-1"
    }


def test_vmss_instance_status_includes_structured_provisioning_errors():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.UNIFORM.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss

    status = MagicMock()
    status.code = "ProvisioningState/failed"
    status.level = "Error"
    status.message = "Allocation failed in zone 1"
    status.display_status = "Provisioning failed"

    member_vm = MagicMock()
    member_vm.instance_id = "3"
    member_vm.name = "vmss-3"
    member_vm.vm_id = "vm-guid-3"
    member_vm.instance_view.statuses = [status]
    member_vm.hardware_profile.vm_size = "Standard_D4s_v5"
    member_vm.location = "eastus2"
    member_vm.zones = ["1"]

    azure_client.compute_client.virtual_machine_scale_set_vms.list.return_value = [member_vm]

    request = MagicMock()
    request.resource_ids = ["vmss-azure-test"]
    request.metadata = {"resource_group": "test-rg"}

    result = handler.check_hosts_status(request)

    assert result[0]["status"] == "failed"
    assert result[0]["provider_data"]["fleet_errors"][0]["error_code"] == "ProvisioningStateFailed"
    assert "Allocation failed" in result[0]["provider_data"]["fleet_errors"][0]["error_message"]


def test_vmss_status_raises_when_strict_mode_and_listing_fails():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    azure_client.compute_client.virtual_machine_scale_sets.get.side_effect = Exception("transient ARM failure")

    request = MagicMock()
    request.resource_ids = ["vmss-azure-test"]
    request.metadata = {
        "resource_group": "test-rg",
        "fail_on_partial_status_error": True,
    }

    with pytest.raises(RuntimeError, match="Failed to list instances for VMSS 'vmss-azure-test'"):
        handler.check_hosts_status(request)


def test_vmss_status_populates_network_identity():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.UNIFORM.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss

    nic_ref = MagicMock()
    nic_ref.id = (
        "/subscriptions/sub/resourceGroups/test-rg/providers/"
        "Microsoft.Network/networkInterfaces/nic-vmss-1"
    )
    nic_ref.properties.primary = True

    member_vm = MagicMock()
    member_vm.instance_id = "3"
    member_vm.name = "vmss-3"
    member_vm.vm_id = "vm-guid-3"
    member_vm.instance_view.statuses = []
    member_vm.hardware_profile.vm_size = "Standard_D4s_v5"
    member_vm.location = "eastus2"
    member_vm.zones = ["1"]
    member_vm.network_profile.network_interfaces = [nic_ref]
    azure_client.resolve_network_identity_from_vm.return_value = {
        "private_ip": "10.0.0.7",
        "public_ip": None,
        "subnet_id": (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/virtualNetworks/test-vnet/subnets/default"
        ),
        "vnet_id": (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/virtualNetworks/test-vnet"
        ),
        "nic_id": nic_ref.id,
        "nic_name": "nic-vmss-1",
    }

    azure_client.compute_client.virtual_machine_scale_set_vms.list.return_value = [member_vm]

    request = MagicMock()
    request.resource_ids = ["vmss-azure-test"]
    request.metadata = {"resource_group": "test-rg"}

    result = handler.check_hosts_status(request)

    assert result[0]["private_ip"] == "10.0.0.7"
    assert result[0]["subnet_id"].endswith("/subnets/default")
    assert result[0]["vpc_id"].endswith("/virtualNetworks/test-vnet")
    assert result[0]["provider_data"]["nic_name"] == "nic-vmss-1"


def test_vmss_resource_errors_surface_failed_scale_set_without_instances():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    vmss = MagicMock()
    vmss.provisioning_state = "Failed"
    vmss.statuses = []
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss

    errors = handler.get_vmss_resource_errors("test-rg", "vmss-azure-test")

    assert errors[0]["error_code"] == "ProvisioningStateFailed"
    assert errors[0]["instance_id"] == "vmss-azure-test"


def test_vmss_release_deletes_only_requested_uniform_instances():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)
    handler._resolve_vmss_instance_ids = MagicMock(return_value=["3", "4"])
    handler._list_vmss_instances = MagicMock(  # type: ignore[method-assign]
        return_value=[
            {"instance_id": "3"},
            {"instance_id": "4"},
            {"instance_id": "5"},
        ]
    )

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.UNIFORM.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss

    delete_poller = MagicMock()
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete_instances.return_value = (
        delete_poller
    )

    handler.release_hosts(
        machine_ids=["3", "4"],
        resource_id="vmss-azure-test",
        context={"resource_group": "test-rg"},
    )

    delete_call = (
        azure_client.compute_client.virtual_machine_scale_sets.begin_delete_instances.call_args.kwargs
    )
    assert delete_call["resource_group_name"] == "test-rg"
    assert delete_call["vm_scale_set_name"] == "vmss-azure-test"
    delete_ids = delete_call["vm_instance_i_ds"]
    if hasattr(delete_ids, "instance_ids"):
        assert delete_ids.instance_ids == ["3", "4"]
    else:
        assert delete_ids["instance_ids"] == ["3", "4"]


def test_vmss_release_deletes_flexible_members_without_cleanup_metadata_when_vmss_not_empty():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)
    handler._list_vmss_instances = MagicMock(  # type: ignore[method-assign]
        return_value=[
            {"instance_id": "vm-a"},
            {"instance_id": "vm-b"},
            {"instance_id": "vm-c"},
        ]
    )

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.FLEXIBLE.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss

    azure_client.compute_client.virtual_machines.begin_delete.return_value = MagicMock()

    result = handler.release_hosts(
        machine_ids=["vm-a", "vm-b"],
        resource_id="vmss-azure-test",
        context={"resource_group": "test-rg"},
    )

    assert result["provider_data"]["operation_status"] == "submitted"
    assert "pending_vmss_cleanup" not in result["provider_data"]
    assert _deleted_vm_names(azure_client) == ["vm-a", "vm-b"]
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete.assert_not_called()


def test_vmss_release_marks_flexible_vmss_for_cleanup_when_last_instance_is_returned():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)
    handler._list_vmss_instances = MagicMock(  # type: ignore[method-assign]
        return_value=[{"instance_id": "vm-a"}]
    )

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.FLEXIBLE.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss
    azure_client.compute_client.virtual_machines.begin_delete.return_value = MagicMock()

    result = handler.release_hosts(
        machine_ids=["vm-a"],
        resource_id="vmss-azure-test",
        context={"resource_group": "test-rg"},
    )

    assert result["provider_data"]["operation_status"] == "submitted"
    assert result["provider_data"]["pending_vmss_cleanup"] == {
        "resource_group": "test-rg",
        "vmss_name": "vmss-azure-test",
        "machine_ids": ["vm-a"],
        "delete_vmss_when_empty": True,
    }
    assert _deleted_vm_names(azure_client) == ["vm-a"]
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete.assert_called_once_with(
        resource_group_name="test-rg",
        vm_scale_set_name="vmss-azure-test",
    )


def test_vmss_release_marks_flexible_vmss_for_cleanup_when_last_instance_id_shape_differs():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)
    handler._list_vmss_instances = MagicMock(  # type: ignore[method-assign]
        return_value=[{"instance_id": "vm-a"}]
    )

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.FLEXIBLE.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss
    azure_client.compute_client.virtual_machines.begin_delete.return_value = MagicMock()

    result = handler.release_hosts(
        machine_ids=["guid-a"],
        resource_id="vmss-azure-test",
        context={"resource_group": "test-rg"},
    )

    assert result["provider_data"]["pending_vmss_cleanup"] == {
        "resource_group": "test-rg",
        "vmss_name": "vmss-azure-test",
        "machine_ids": ["guid-a"],
        "delete_vmss_when_empty": True,
    }


def test_vmss_release_marks_uniform_vmss_for_cleanup_when_last_instance_is_returned():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)
    handler._resolve_vmss_instance_ids = MagicMock(return_value=["3"])
    handler._list_vmss_instances = MagicMock(  # type: ignore[method-assign]
        return_value=[{"instance_id": "3"}]
    )

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.UNIFORM.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss

    delete_instances_poller = MagicMock()
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete_instances.return_value = (
        delete_instances_poller
    )
    result = handler.release_hosts(
        machine_ids=["3"],
        resource_id="vmss-azure-test",
        context={"resource_group": "test-rg"},
    )

    delete_call = (
        azure_client.compute_client.virtual_machine_scale_sets.begin_delete_instances.call_args.kwargs
    )
    delete_ids = delete_call["vm_instance_i_ds"]
    if hasattr(delete_ids, "instance_ids"):
        assert delete_ids.instance_ids == ["3"]
    else:
        assert delete_ids["instance_ids"] == ["3"]
    assert result["provider_data"]["pending_vmss_cleanup"] == {
        "resource_group": "test-rg",
        "vmss_name": "vmss-azure-test",
        "machine_ids": ["3"],
        "delete_vmss_when_empty": True,
    }
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete.assert_called_once_with(
        resource_group_name="test-rg",
        vm_scale_set_name="vmss-azure-test",
    )


def test_vmss_delete_vmss_for_flexible_vmss_defers_scale_set_delete_until_empty():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)
    handler._list_vmss_instances = MagicMock(  # type: ignore[method-assign]
        return_value=[
            {"instance_id": "vm-a"},
            {"instance_id": "vm-b"},
        ]
    )

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.FLEXIBLE.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss
    azure_client.compute_client.virtual_machines.begin_delete.return_value = MagicMock()

    result = handler.release_hosts(
        machine_ids=[],
        resource_id="vmss-azure-test",
        context={"resource_group": "test-rg", "delete_vmss": True},
    )

    assert result["provider_data"]["pending_vmss_cleanup"] == {
        "resource_group": "test-rg",
        "vmss_name": "vmss-azure-test",
        "machine_ids": ["vm-a", "vm-b"],
        "delete_vmss_when_empty": True,
    }
    assert _deleted_vm_names(azure_client) == ["vm-a", "vm-b"]
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete.assert_not_called()


def test_single_vm_status_populates_network_identity():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    nic_ref = MagicMock()
    nic_ref.id = (
        "/subscriptions/sub/resourceGroups/test-rg/providers/"
        "Microsoft.Network/networkInterfaces/nic-vm-1"
    )
    nic_ref.properties.primary = True

    vm = MagicMock()
    vm.name = "vm-1"
    vm.vm_id = "vm-guid-1"
    vm.instance_view.statuses = []
    vm.hardware_profile.vm_size = "Standard_D4s_v5"
    vm.location = "eastus2"
    vm.zones = ["1"]
    vm.network_profile.network_interfaces = [nic_ref]
    azure_client.resolve_network_identity_from_vm.return_value = {
        "private_ip": "10.0.0.4",
        "public_ip": "52.1.2.3",
        "subnet_id": (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/virtualNetworks/test-vnet/subnets/default"
        ),
        "vnet_id": (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/virtualNetworks/test-vnet"
        ),
        "nic_id": nic_ref.id,
        "nic_name": "nic-vm-1",
    }
    azure_client.compute_client.virtual_machines.get.return_value = vm

    request = MagicMock()
    request.resource_ids = ["vm-1"]
    request.metadata = {"resource_group": "test-rg"}

    result = handler.check_hosts_status(request)

    assert result[0]["private_ip"] == "10.0.0.4"
    assert result[0]["public_ip"] == "52.1.2.3"
    assert result[0]["subnet_id"].endswith("/subnets/default")
    assert result[0]["vpc_id"].endswith("/virtualNetworks/test-vnet")


def test_single_vm_release_returns_submitted_delete_metadata():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    vm_1 = MagicMock()
    vm_1.name = "vm-1"
    vm_1.vm_id = "guid-1"
    vm_2 = MagicMock()
    vm_2.name = "vm-2"
    vm_2.vm_id = "guid-2"
    azure_client.compute_client.virtual_machines.list.return_value = [vm_1, vm_2]

    azure_client.compute_client.virtual_machines.begin_delete.return_value = MagicMock()

    result = handler.release_hosts(
        machine_ids=["guid-1", "guid-2"],
        resource_id="unused",
        context={"resource_group": "test-rg"},
    )

    assert result["provider_data"]["operation_status"] == "submitted"
    assert result["provider_data"]["submitted_deletions"] == [
        {"requested_id": "guid-1", "vm_name": "vm-1"},
        {"requested_id": "guid-2", "vm_name": "vm-2"},
    ]
    assert _deleted_vm_names(azure_client) == ["vm-1", "vm-2"]


def test_single_vm_create_sets_native_delete_options():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    azure_client.resource_client.resources.begin_create_or_update.return_value = MagicMock()

    request = MagicMock()
    request.requested_count = 1
    request.request_id = "req-5"
    request.metadata = {}

    template = AzureTemplate(
        template_id="azure-singlevm-test",
        provider_api="SingleVM",
        vm_size="Standard_D4s_v5",
        resource_group="test-rg",
        location="eastus2",
        network_config={"subnet_id": "/subscriptions/.../subnets/default"},
        ssh_public_keys=["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
        image={
            "publisher": "Canonical",
            "offer": "0001-com-ubuntu-server-jammy",
            "sku": "22_04-lts-gen2",
            "version": "latest",
        },
        data_disks=[{"lun": 0, "disk_size_gb": 128}],
    )

    handler.acquire_hosts(request, template)

    deployment_template = azure_client.resource_client.resources.begin_create_or_update.call_args.kwargs[
        "parameters"
    ]["properties"]["template"]
    vm_resource = next(
        resource
        for resource in deployment_template["resources"]
        if resource["type"] == "Microsoft.Compute/virtualMachines"
    )
    nic_ref = vm_resource["properties"]["networkProfile"]["networkInterfaces"][0]
    assert nic_ref["properties"]["deleteOption"] == "Delete"
    assert vm_resource["properties"]["storageProfile"]["osDisk"]["deleteOption"] == "Delete"
    assert vm_resource["properties"]["storageProfile"]["dataDisks"][0]["deleteOption"] == "Delete"


def test_single_vm_deployment_template_attaches_public_ip_when_enabled():
    template = AzureTemplate(
        template_id="azure-singlevm-test",
        provider_api="SingleVM",
        vm_size="Standard_D4s_v5",
        resource_group="test-rg",
        location="eastus2",
        network_config={
            "subnet_id": "/subscriptions/.../subnets/default",
            "public_ip_enabled": True,
        },
        ssh_public_keys=["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
        image={
            "publisher": "Canonical",
            "offer": "0001-com-ubuntu-server-jammy",
            "sku": "22_04-lts-gen2",
            "version": "latest",
        },
    )
    service = AzureDeploymentService(azure_client=MagicMock(), logger=MagicMock())
    vm_payload = SingleVMHandler._build_vm_params(
        template=template,
        vm_name="vm-test",
        nic_id=service.resource_id_expression(
            "Microsoft.Network/networkInterfaces",
            "nic-vm-test",
        ),
    )
    deployment_template = service.build_single_vm_deployment_template(
        location="eastus2",
        subnet_id="/subscriptions/.../subnets/default",
        vm_definitions=[
            {
                "vm_name": "vm-test",
                "nic_name": "nic-vm-test",
                "public_ip_name": "pip-vm-test",
                "vm_payload": vm_payload,
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
