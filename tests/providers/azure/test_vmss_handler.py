"""Focused tests for VMSS handler behavior."""

from unittest.mock import MagicMock

from providers.azure.domain.template.value_objects import AzureVMSSOrchestrationMode
from providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from providers.azure.infrastructure.handlers.single_vm_handler import SingleVMHandler
from providers.azure.infrastructure.handlers.vmss_handler import VMSSHandler


def _make_template() -> AzureTemplate:
    return AzureTemplate(
        template_id="azure-vmss-test",
        provider_api="VMSS",
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
    assert result["provider_data"]["continuation_token"] == "vmss-lro-token"
    poller.result.assert_not_called()


def test_vmss_native_spec_override_is_used_for_create_payload():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = VMSSHandler(azure_client=azure_client, logger=logger)
    handler.azure_native_spec_service = MagicMock()

    poller = MagicMock()
    azure_client.compute_client.virtual_machine_scale_sets.begin_create_or_update.return_value = poller

    request = MagicMock()
    request.requested_count = 3
    request.request_id = "req-native"
    request.metadata = {}

    template = _make_template(provider_api_spec={"location": "westus2"})
    merged_payload = {
        "location": "westus2",
        "sku": {"name": "Standard_D8s_v5"},
        "properties": {"virtualMachineProfile": {}},
    }
    handler.azure_native_spec_service.process_provider_api_spec_with_merge.return_value = (
        merged_payload
    )

    result = handler.acquire_hosts(request, template)

    assert result["success"] is True
    call = azure_client.compute_client.virtual_machine_scale_sets.begin_create_or_update.call_args
    assert call.kwargs["parameters"]["location"] == "westus2"
    assert call.kwargs["parameters"]["sku"]["capacity"] == 3


def test_flexible_vmss_status_uses_virtual_machines_listing():
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
    azure_client.compute_client.virtual_machines.list.assert_called_once_with(
        resource_group_name="test-rg"
    )
    azure_client.compute_client.virtual_machine_scale_set_vms.list.assert_not_called()


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
    assert result["provider_data"]["submitted_vms"][0]["continuation_token"] == "single-vm-lro-token"
    vm_poller.result.assert_not_called()


def test_single_vm_native_spec_override_is_used_for_create_payload():
    azure_client = MagicMock()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)
    handler.azure_native_spec_service = MagicMock()

    nic_result = MagicMock()
    nic_result.id = "/subscriptions/.../networkInterfaces/nic-vm-1"
    nic_poller = MagicMock()
    nic_poller.result.return_value = nic_result
    azure_client.network_client.network_interfaces.begin_create_or_update.return_value = nic_poller

    vm_poller = MagicMock()
    azure_client.compute_client.virtual_machines.begin_create_or_update.return_value = vm_poller

    request = MagicMock()
    request.requested_count = 1
    request.request_id = "req-native"
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
        provider_api_spec={"tags": {"Mode": "native"}},
    )
    handler.azure_native_spec_service.process_provider_api_spec_with_merge.return_value = {
        "location": "eastus2",
        "properties": {
            "hardwareProfile": {"vmSize": "Standard_D8s_v5"},
            "storageProfile": {"imageReference": {"publisher": "Canonical"}},
            "osProfile": {"computerName": "vm-short", "adminUsername": "azureuser"},
            "networkProfile": {
                "networkInterfaces": [{"id": nic_result.id, "properties": {"primary": True}}]
            },
        },
        "tags": {"Mode": "native"},
    }

    result = handler.acquire_hosts(request, template)

    assert result["success"] is True
    call = azure_client.compute_client.virtual_machines.begin_create_or_update.call_args
    assert call.kwargs["parameters"]["tags"]["Mode"] == "native"
    assert call.kwargs["parameters"]["properties"]["hardwareProfile"]["vmSize"] == "Standard_D8s_v5"


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
