"""Focused tests for VMSS handler behavior."""

from unittest.mock import MagicMock

from providers.azure.domain.template.value_objects import AzureVMSSOrchestrationMode
from providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from providers.azure.infrastructure.handlers.single_vm_handler import SingleVMHandler
from providers.azure.infrastructure.handlers.vmss_handler import VMSSHandler


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


def test_acquire_hosts_returns_immediately_after_submitting_lro():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)

    poller = MagicMock()
    poller.continuation_token.return_value = "vmss-lro-token"
    azure_client.compute_client.virtual_machine_scale_sets.begin_create_or_update.return_value = poller

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


def test_single_vm_acquire_hosts_returns_immediately_after_submitting_lros():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    nic_result = MagicMock()
    nic_result.id = "/subscriptions/.../networkInterfaces/nic-vm-1"
    nic_poller = MagicMock()
    nic_poller.result.return_value = nic_result
    azure_client.network_client.network_interfaces.begin_create_or_update.return_value = nic_poller

    vm_poller = MagicMock()
    vm_poller.continuation_token.return_value = "single-vm-lro-token"
    azure_client.compute_client.virtual_machines.begin_create_or_update.return_value = vm_poller

    request = MagicMock()
    request.requested_count = 1
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
    assert len(result["resource_ids"]) == 1
    assert result["instances"] == []
    assert result["provider_data"]["operation_status"] == "submitted"


def test_single_vm_partial_failure_returns_structured_errors():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    nic_result = MagicMock()
    nic_result.id = "/subscriptions/.../networkInterfaces/nic-vm-1"
    nic_poller = MagicMock()
    nic_poller.result.return_value = nic_result
    azure_client.network_client.network_interfaces.begin_create_or_update.return_value = nic_poller

    first_vm_poller = MagicMock()
    first_vm_poller.continuation_token.return_value = "token-1"
    azure_client.compute_client.virtual_machines.begin_create_or_update.side_effect = [
        first_vm_poller,
        Exception("AllocationFailed: insufficient capacity"),
    ]

    request = MagicMock()
    request.requested_count = 2
    request.request_id = "req-3"
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
    assert len(result["resource_ids"]) == 1
    assert result["provider_data"]["failed_count"] == 1
    assert result["provider_data"]["operation_status"] == "partial_submitted"
    assert result["provider_data"]["fleet_errors"][0]["error_code"] == "AllocationFailed"


def test_single_vm_falls_back_to_alternate_vm_size():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    nic_result = MagicMock()
    nic_result.id = "/subscriptions/.../networkInterfaces/nic-vm-1"
    nic_poller = MagicMock()
    nic_poller.result.return_value = nic_result
    azure_client.network_client.network_interfaces.begin_create_or_update.return_value = nic_poller

    first_failure = Exception("primary size unavailable")
    first_failure.error_code = "AllocationFailed"
    vm_poller = MagicMock()
    vm_poller.continuation_token.return_value = "fallback-token"
    azure_client.compute_client.virtual_machines.begin_create_or_update.side_effect = [
        first_failure,
        vm_poller,
    ]

    request = MagicMock()
    request.requested_count = 1
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
    assert result["provider_data"]["failed_count"] == 0
    assert result["provider_data"]["submitted_vms"][0]["selected_vm_size"] == "Standard_D8s_v5"


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


def test_vmss_release_scales_down_before_deleting_uniform_instances():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)
    handler.azure_resource_manager = MagicMock()
    handler.azure_resource_manager.get_vmss_capacity.return_value = {"capacity": 5}
    handler._resolve_vmss_instance_ids = MagicMock(return_value=["3", "4"])

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

    handler.azure_resource_manager.scale_vmss.assert_called_once_with(
        resource_group="test-rg",
        vmss_name="vmss-azure-test",
        capacity=3,
    )
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete_instances.assert_called_once()
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete.assert_not_called()


def test_vmss_release_submits_flexible_deletes_without_waiting():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)
    handler.azure_resource_manager = MagicMock()
    handler.azure_resource_manager.get_vmss_capacity.return_value = {"capacity": 5}

    vmss = MagicMock()
    vmss.orchestration_mode = AzureVMSSOrchestrationMode.FLEXIBLE.value
    azure_client.compute_client.virtual_machine_scale_sets.get.return_value = vmss

    delete_poller = MagicMock()
    delete_poller.continuation_token.return_value = "token-1"
    delete_poller.result.side_effect = AssertionError("result() should not be called")
    azure_client.compute_client.virtual_machines.begin_delete.return_value = delete_poller

    result = handler.release_hosts(
        machine_ids=["vm-a", "vm-b"],
        resource_id="vmss-azure-test",
        context={"resource_group": "test-rg"},
    )

    assert result["provider_data"]["operation_status"] == "submitted"
    assert result["provider_data"]["pending_reconciliation"]["target_capacity"] == 3
    assert handler.azure_resource_manager.scale_vmss.call_count == 0
    assert azure_client.compute_client.virtual_machines.begin_delete.call_count == 2


def test_vmss_release_deletes_scale_set_when_capacity_reaches_zero():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)
    handler.azure_resource_manager = MagicMock()
    handler.azure_resource_manager.get_vmss_capacity.return_value = {"capacity": 1}
    handler._resolve_vmss_instance_ids = MagicMock(return_value=["3"])

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

    handler.azure_resource_manager.scale_vmss.assert_called_once_with(
        resource_group="test-rg",
        vmss_name="vmss-azure-test",
        capacity=0,
    )
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete_instances.assert_called_once()
    azure_client.compute_client.virtual_machine_scale_sets.begin_delete.assert_not_called()
    assert result["provider_data"]["pending_reconciliation"]["delete_vmss_when_empty"] is True


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


def test_single_vm_release_submits_deletes_without_waiting():
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

    delete_poller = MagicMock()
    delete_poller.continuation_token.return_value = "single-delete-token"
    delete_poller.result.side_effect = AssertionError("result() should not be called")
    azure_client.compute_client.virtual_machines.begin_delete.return_value = delete_poller

    result = handler.release_hosts(
        machine_ids=["guid-1", "guid-2"],
        resource_id="unused",
        context={"resource_group": "test-rg"},
    )

    assert result["provider_data"]["operation_status"] == "submitted"
    assert result["provider_data"]["cleanup_deferred"] is True
    assert len(result["provider_data"]["submitted_deletions"]) == 2
    assert azure_client.compute_client.virtual_machines.begin_delete.call_count == 2
