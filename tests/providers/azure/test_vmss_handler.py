"""Focused tests for VMSS handler behavior."""

import pytest
from unittest.mock import MagicMock

import orb.providers.azure.infrastructure.handlers.vmss_handler as vmss_handler_module
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import AzureVMSSOrchestrationMode
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureReleaseContext
from orb.providers.azure.infrastructure.handlers.vmss_handler import VMSSHandler


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


def test_acquire_hosts_does_not_mutate_template_when_network_config_is_derived_from_subnet_ids():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    azure_client.compute_client.virtual_machine_scale_sets.begin_create_or_update.return_value = (
        MagicMock()
    )

    request = MagicMock()
    request.requested_count = 1
    request.request_id = "req-network-copy"
    request.metadata = {}

    template = _make_template(
        network_config=None,
        subnet_ids=["/subscriptions/.../subnets/derived"],
    )

    result = handler.acquire_hosts(request, template)

    assert result["success"] is True
    assert template.network_config is None
    create_call = azure_client.compute_client.virtual_machine_scale_sets.begin_create_or_update.call_args
    subnet_id = create_call.kwargs["parameters"]["properties"]["virtualMachineProfile"][
        "networkProfile"
    ]["networkInterfaceConfigurations"][0]["properties"]["ipConfigurations"][0]["properties"][
        "subnet"
    ]["id"]
    assert subnet_id == "/subscriptions/.../subnets/derived"


def test_flexible_vmss_status_returns_only_member_vms():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.FLEXIBLE.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss

    member_vm = MagicMock()
    member_vm.name = "vmss-azure-test_abcd1234"
    member_vm.instance_id = "vmss-azure-test_abcd1234"
    member_vm.vm_id = "vm-guid-1"
    member_vm.instance_view.statuses = []
    member_vm.hardware_profile.vm_size = "Standard_D4s_v5"
    member_vm.location = "eastus2"
    member_vm.zones = ["1"]

    azure_client.subscription_id = "sub"
    azure_client.compute_client.virtual_machines.list.return_value = [member_vm]

    request = MagicMock()
    request.resource_ids = ["vmss-azure-test"]
    request.metadata = {"resource_group": "test-rg"}

    result = handler.check_hosts_status(request)

    assert len(result) == 1
    assert result[0]["instance_id"] == "vmss-azure-test_abcd1234"
    azure_client.compute_client.virtual_machines.list.assert_called_once_with(
        resource_group_name="test-rg",
        filter="'virtualMachineScaleSet/id' eq '/subscriptions/sub/resourceGroups/test-rg/providers/Microsoft.Compute/virtualMachineScaleSets/vmss-azure-test'",
        expand="instanceView",
    )


def test_flexible_vmss_listing_uses_azure_side_filter_without_client_side_membership_scan():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    azure_client.subscription_id = "sub"
    azure_client.compute_client.virtual_machines.list.return_value = []

    result = handler._list_vmss_instances(
        resource_group="test-rg",
        vmss_name="vmss-azure-test",
        include_instance_view=False,
        orchestration_mode=AzureVMSSOrchestrationMode.FLEXIBLE,
    )

    assert result == []
    azure_client.compute_client.virtual_machines.list.assert_called_once_with(
        resource_group_name="test-rg",
        filter="'virtualMachineScaleSet/id' eq '/subscriptions/sub/resourceGroups/test-rg/providers/Microsoft.Compute/virtualMachineScaleSets/vmss-azure-test'",
    )


def test_flexible_vmss_status_uses_filtered_list_result_directly():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.FLEXIBLE.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss

    member_vm = MagicMock()
    member_vm.name = "vmss-azure-test_abcd1234"
    member_vm.instance_id = "vmss-azure-test_abcd1234"
    member_vm.vm_id = "vm-guid-1"
    member_vm.instance_view.statuses = []
    member_vm.hardware_profile.vm_size = "Standard_D4s_v5"
    member_vm.location = "eastus2"
    member_vm.zones = ["1"]

    azure_client.subscription_id = "sub"
    azure_client.compute_client.virtual_machines.list.return_value = [member_vm]

    request = MagicMock()
    request.resource_ids = ["vmss-azure-test"]
    request.metadata = {"resource_group": "test-rg"}

    result = handler.check_hosts_status(request)

    assert len(result) == 1
    assert result[0]["instance_id"] == "vmss-azure-test_abcd1234"
    azure_client.compute_client.virtual_machines.list.assert_called_once_with(
        resource_group_name="test-rg",
        filter="'virtualMachineScaleSet/id' eq '/subscriptions/sub/resourceGroups/test-rg/providers/Microsoft.Compute/virtualMachineScaleSets/vmss-azure-test'",
        expand="instanceView",
    )


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


def test_vmss_status_still_returns_instance_when_network_identity_resolution_fails():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.UNIFORM.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss
    azure_client.resolve_network_identity_from_vm.side_effect = AttributeError(
        "missing network property"
    )

    member_vm = MagicMock()
    member_vm.instance_id = "3"
    member_vm.name = "vmss-3"
    member_vm.vm_id = "vm-guid-3"
    member_vm.instance_view.statuses = []
    member_vm.hardware_profile.vm_size = "Standard_D4s_v5"
    member_vm.location = "eastus2"
    member_vm.zones = ["1"]

    azure_client.compute_client.virtual_machine_scale_set_vms.list.return_value = [member_vm]

    request = MagicMock()
    request.resource_ids = ["vmss-azure-test"]
    request.metadata = {"resource_group": "test-rg"}

    result = handler.check_hosts_status(request)

    assert len(result) == 1
    assert result[0]["instance_id"] == "3"
    assert result[0]["private_ip"] is None
    assert result[0]["provider_data"]["nic_id"] is None
    logger.warning.assert_called()


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


def test_vmss_resource_errors_logs_and_returns_empty_list_when_vmss_lookup_fails():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    azure_client.compute_client.virtual_machine_scale_sets.get.side_effect = RuntimeError("boom")

    errors = handler.get_vmss_resource_errors("test-rg", "vmss-azure-test")

    assert errors == []
    logger.warning.assert_called_once()


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
        context=AzureReleaseContext(resource_group="test-rg"),
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
        context=AzureReleaseContext(resource_group="test-rg"),
    )

    assert result["provider_data"]["operation_status"] == "submitted"
    assert "pending_resource_cleanup" not in result["provider_data"]
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
        context=AzureReleaseContext(resource_group="test-rg"),
    )

    assert result["provider_data"]["operation_status"] == "submitted"
    assert result["provider_data"]["pending_resource_cleanup"] == {
        "resource_group": "test-rg",
        "vmss_name": "vmss-azure-test",
        "machine_ids": ["vm-a"],
        "delete_vmss_when_empty": True,
        "member_delete_submitted": True,
        "delete_submission_semantics": "best_effort_without_reverification",
        "delete_submitted": True,
        "delete_retry_pending": False,
    }
    assert _deleted_vm_names(azure_client) == ["vm-a"]
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete.assert_called_once_with(
        resource_group_name="test-rg",
        vm_scale_set_name="vmss-azure-test",
    )


def test_vmss_release_does_not_mark_flexible_vmss_for_cleanup_when_requested_ids_do_not_match_members():
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
        machine_ids=["guid-a", "guid-b", "guid-c"],
        resource_id="vmss-azure-test",
        context=AzureReleaseContext(resource_group="test-rg"),
    )

    assert "pending_resource_cleanup" not in result["provider_data"]
    assert _deleted_vm_names(azure_client) == ["guid-a", "guid-b", "guid-c"]
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete.assert_not_called()


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
        context=AzureReleaseContext(resource_group="test-rg"),
    )

    delete_call = (
        azure_client.compute_client.virtual_machine_scale_sets.begin_delete_instances.call_args.kwargs
    )
    delete_ids = delete_call["vm_instance_i_ds"]
    if hasattr(delete_ids, "instance_ids"):
        assert delete_ids.instance_ids == ["3"]
    else:
        assert delete_ids["instance_ids"] == ["3"]
    assert result["provider_data"]["pending_resource_cleanup"] == {
        "resource_group": "test-rg",
        "vmss_name": "vmss-azure-test",
        "machine_ids": ["3"],
        "delete_vmss_when_empty": True,
        "member_delete_submitted": True,
        "delete_submission_semantics": "best_effort_without_reverification",
        "delete_submitted": True,
        "delete_retry_pending": False,
    }
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete.assert_called_once_with(
        resource_group_name="test-rg",
        vm_scale_set_name="vmss-azure-test",
    )


def test_vmss_release_surfaces_retry_pending_when_immediate_empty_vmss_delete_fails():
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
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete.side_effect = RuntimeError(
        "scale set still has deleting members"
    )

    result = handler.release_hosts(
        machine_ids=["vm-a"],
        resource_id="vmss-azure-test",
        context=AzureReleaseContext(resource_group="test-rg"),
    )

    assert result["provider_data"]["pending_resource_cleanup"] == {
        "resource_group": "test-rg",
        "vmss_name": "vmss-azure-test",
        "machine_ids": ["vm-a"],
        "delete_vmss_when_empty": True,
        "member_delete_submitted": True,
        "delete_submission_semantics": "best_effort_without_reverification",
        "delete_submitted": False,
        "delete_retry_pending": True,
        "last_delete_error": "scale set still has deleting members",
    }


def test_acquire_hosts_handles_missing_azure_error_message_without_key_error(monkeypatch):
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    azure_client.compute_client.virtual_machine_scale_sets.begin_create_or_update.side_effect = (
        RuntimeError("quota exceeded")
    )

    monkeypatch.setattr(
        vmss_handler_module,
        "extract_azure_error_details",
        lambda exc: {"raw_error_code": None, "status_code": None},
    )

    request = MagicMock()
    request.requested_count = 1
    request.request_id = "req-null-message"
    request.metadata = {}

    with pytest.raises(Exception) as exc_info:
        handler.acquire_hosts(request, _make_template())

    assert exc_info.type.__name__ == "QuotaExceededError"
