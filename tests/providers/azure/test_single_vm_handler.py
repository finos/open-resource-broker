"""Focused tests for SingleVM handler behavior."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from orb.providers.azure.exceptions.azure_exceptions import LaunchError, TerminationError
from orb.providers.azure.infrastructure.handlers.azure_handler import (
    AzureReleaseContext,
    RAISE_ON_STATUS_ERROR_METADATA_KEY,
)
from orb.providers.azure.infrastructure.handlers.single_vm_handler import (
    SingleVMHandler,
    _format_azure_error_message,
)
from tests.providers.azure.strategy_test_support import (
    AsyncPager,
    make_azure_template,
    make_single_vm_azure_client,
    run_operation,
)


def test_format_azure_error_message_includes_nested_details():
    message = _format_azure_error_message({
        "message": "Deployment validation failed",
        "details": [
            {
                "code": "InvalidParameter",
                "message": "The supplied VM size is not available in this location.",
            }
        ],
    })

    assert message == (
        "InvalidParameter: The supplied VM size is not available in this location."
    )


def test_format_azure_error_message_falls_back_to_primary_message():
    assert (
        _format_azure_error_message({"error_message": "template is invalid"})
        == "template is invalid"
    )


def _make_request(*, count: int = 1, request_id: str = "req-1", metadata=None):
    request = MagicMock()
    request.requested_count = count
    request.request_id = request_id
    request.metadata = metadata or {}
    return request


def _deleted_vm_names(azure_client: MagicMock) -> list[str]:
    return [
        str(call.kwargs["vm_name"])
        for call in azure_client.compute_client.virtual_machines.begin_delete.call_args_list
    ]

def _make_template(**overrides):
    return make_azure_template(
        template_id="azure-singlevm-test",
        provider_api="SingleVM",
        **overrides,
    )


def _make_azure_client() -> MagicMock:
    return make_single_vm_azure_client()


def test_acquire_hosts_submits_one_batched_deployment_and_returns_submitted_status():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    azure_client.resource_client.resources.begin_create_or_update.return_value = MagicMock()

    result = run_operation(
        handler.acquire_hosts_async(_make_request(count=2, request_id="req-2"), _make_template())
    )

    assert result["success"] is True
    assert len(result["resource_ids"]) == 2
    assert result["provider_data"]["operation_status"] == "submitted"
    deployment_call = azure_client.resource_client.resources.begin_create_or_update.call_args.kwargs
    deployment_template = deployment_call["parameters"]["properties"]["template"]
    resource_types = [resource["type"] for resource in deployment_template["resources"]]
    assert resource_types.count("Microsoft.Network/networkInterfaces") == 2
    assert resource_types.count("Microsoft.Compute/virtualMachines") == 2
    assert result["provider_data"]["deployment_name"] == deployment_call["resource_name"]
    assert len(result["provider_data"]["submitted_vms"]) == 2


def test_acquire_hosts_creates_public_ips_when_enabled():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    azure_client.resource_client.resources.begin_create_or_update.return_value = MagicMock()

    template = _make_template(
        network_config={
            "subnet_id": "/subscriptions/.../subnets/default",
            "public_ip_enabled": True,
        }
    )

    result = run_operation(
        handler.acquire_hosts_async(_make_request(count=2, request_id="req-pip"), template)
    )

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


@pytest.mark.asyncio
async def test_acquire_hosts_async_submits_one_batched_deployment_and_returns_submitted_status():
    azure_client = _make_azure_client()
    azure_client.get_async_compute_client = AsyncMock(return_value=MagicMock())
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)
    handler.azure_deployment_service.submit_template_deployment_async = AsyncMock(
        return_value="dep-async"
    )

    result = await handler.acquire_hosts_async(
        _make_request(count=2, request_id="req-async"),
        _make_template(),
    )

    assert result["success"] is True
    assert result["provider_data"]["deployment_name"] == "dep-async"
    assert result["provider_data"]["submitted_count"] == 2


def test_acquire_hosts_falls_back_to_alternate_vm_size_for_the_whole_batch():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    first_failure = Exception("primary size unavailable")
    first_failure.error_code = "AllocationFailed"
    azure_client.resource_client.resources.begin_create_or_update.side_effect = [
        first_failure,
        MagicMock(),
    ]

    template = _make_template(vm_sizes=["Standard_D8s_v5"])

    result = run_operation(
        handler.acquire_hosts_async(_make_request(count=2, request_id="req-4"), template)
    )

    assert result["success"] is True
    assert result["provider_data"]["submitted_count"] == 2
    assert all(
        submitted_vm["selected_vm_size"] == "Standard_D8s_v5"
        for submitted_vm in result["provider_data"]["submitted_vms"]
    )


def test_acquire_hosts_stops_after_non_capacity_error():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    failure = Exception("template is invalid")
    failure.error_code = "InvalidParameter"
    azure_client.resource_client.resources.begin_create_or_update.side_effect = failure

    template = _make_template(vm_sizes=["Standard_D8s_v5"])

    with pytest.raises(LaunchError, match="template is invalid"):
        run_operation(
            handler.acquire_hosts_async(_make_request(count=2, request_id="req-invalid"), template)
        )

    assert azure_client.resource_client.resources.begin_create_or_update.call_count == 1


def test_acquire_hosts_classifies_quota_runtime_error():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    handler.azure_deployment_service.submit_template_deployment_async = AsyncMock(
        side_effect=RuntimeError("quota exceeded")
    )

    with pytest.raises(LaunchError) as exc_info:
        run_operation(
            handler.acquire_hosts_async(_make_request(count=1, request_id="req-quota"), _make_template())
        )

    assert exc_info.value.error_code == "QuotaExceeded"


def test_acquire_hosts_does_not_misclassify_resource_name_containing_quota():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    fake_error = MagicMock()
    fake_error.code = "InvalidParameter"
    fake_error.message = "subnet 'team-quota-subnet-1' does not exist"

    response = MagicMock()
    response.status_code = 400

    exc = HttpResponseError(response=response)
    exc.error = fake_error
    handler.azure_deployment_service.submit_template_deployment_async = AsyncMock(
        side_effect=exc
    )

    with pytest.raises(LaunchError) as exc_info:
        run_operation(
            handler.acquire_hosts_async(
                _make_request(count=1, request_id="req-quota-in-name"),
                _make_template(),
            )
        )

    assert exc_info.value.error_code == "InvalidParameter"


def test_acquire_hosts_requires_subnet_id():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    template = _make_template(network_config=None, subnet_ids=[])

    with pytest.raises(LaunchError, match="No subnet specified"):
        run_operation(handler.acquire_hosts_async(_make_request(), template))


def test_acquire_hosts_resolves_ssh_key_name_without_mutating_original_template():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)
    azure_client.resource_client.resources.begin_create_or_update.return_value = MagicMock()

    template = _make_template(
        ssh_public_keys=[],
        ssh_key_name="orb-key",
    )

    with patch(
        "orb.providers.azure.infrastructure.services.ssh_key_resolver.resolve_ssh_keys_async",
        new=AsyncMock(return_value=["ssh-rsa resolved test@host"]),
    ) as mock_resolve:
        result = run_operation(
            handler.acquire_hosts_async(_make_request(request_id="req-ssh"), template)
        )

    assert result["success"] is True
    assert template.ssh_public_keys == []
    mock_resolve.assert_called_once_with(
        ssh_key_name="orb-key",
        ssh_public_keys=[],
        resource_group="test-rg",
        compute_client=mock_resolve.call_args.kwargs["compute_client"],
    )


def test_status_populates_network_identity():
    azure_client = _make_azure_client()
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
    azure_client.resolve_network_identity_from_vm_async = AsyncMock(
        return_value={
        "private_ip": "10.0.0.4",
        "public_ip": "52.1.2.3",
        "subnet_id": "/subscriptions/sub/.../subnets/default",
        "vnet_id": "/subscriptions/sub/.../virtualNetworks/test-vnet",
        "nic_id": nic_ref.id,
        "nic_name": "nic-vm-1",
        }
    )
    azure_client.compute_client.virtual_machines.get.return_value = vm

    request = MagicMock()
    request.resource_ids = ["vm-1"]
    request.metadata = {"resource_group": "test-rg"}

    result = run_operation(handler.check_hosts_status_async(request))

    assert result[0]["private_ip"] == "10.0.0.4"
    assert result[0]["public_ip"] == "52.1.2.3"
    assert result[0]["subnet_id"].endswith("/subnets/default")
    assert result[0]["vpc_id"].endswith("/virtualNetworks/test-vnet")


@pytest.mark.asyncio
async def test_check_hosts_status_async_populates_network_identity():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    vm = MagicMock()
    vm.name = "vm-1"
    vm.vm_id = "vm-guid-1"
    vm.instance_view.statuses = []
    vm.hardware_profile.vm_size = "Standard_D4s_v5"
    vm.location = "eastus2"
    vm.zones = ["1"]

    async_compute = MagicMock()
    async_compute.virtual_machines.get = AsyncMock(return_value=vm)
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute)
    handler._resolve_vm_names_async = AsyncMock(return_value=["vm-1"])
    azure_client.resolve_network_identity_from_vm_async = AsyncMock(
        return_value={
            "private_ip": "10.0.0.4",
            "public_ip": "52.1.2.3",
            "subnet_id": "/subscriptions/sub/.../subnets/default",
            "vnet_id": "/subscriptions/sub/.../virtualNetworks/test-vnet",
            "nic_id": "nic-id",
            "nic_name": "nic-name",
        }
    )

    request = MagicMock()
    request.resource_ids = ["vm-1"]
    request.metadata = {"resource_group": "test-rg"}

    result = await handler.check_hosts_status_async(request)

    assert result[0]["instance_id"] == "vm-1"
    assert result[0]["public_ip"] == "52.1.2.3"


def test_status_still_returns_instance_when_network_identity_resolution_fails():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)
    azure_client.resolve_network_identity_from_vm_async = AsyncMock(
        side_effect=AttributeError("missing network property")
    )

    vm = MagicMock()
    vm.name = "vm-single"
    vm.vm_id = "vm-guid-1"
    vm.instance_view.statuses = []
    vm.hardware_profile.vm_size = "Standard_D4s_v5"
    vm.location = "eastus2"
    vm.zones = ["1"]

    azure_client.compute_client.virtual_machines.get.return_value = vm

    request = MagicMock()
    request.resource_ids = ["vm-single"]
    request.metadata = {"resource_group": "test-rg"}

    result = run_operation(handler.check_hosts_status_async(request))

    assert len(result) == 1
    assert result[0]["instance_id"] == "vm-single"
    assert result[0]["private_ip"] is None
    assert result[0]["provider_data"]["nic_id"] is None
    logger.warning.assert_called()


@pytest.mark.asyncio
async def test_status_best_effort_returns_partial_results_when_one_vm_lookup_fails():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    vm = MagicMock()
    vm.name = "vm-1"
    vm.vm_id = "vm-guid-1"
    vm.instance_view.statuses = []
    vm.hardware_profile.vm_size = "Standard_D4s_v5"
    vm.location = "eastus2"
    vm.zones = ["1"]

    async_compute = MagicMock()
    async_compute.virtual_machines.get = AsyncMock(side_effect=[vm, RuntimeError("boom")])
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute)
    handler._resolve_vm_names_async = AsyncMock(return_value=["vm-1", "vm-2"])
    azure_client.resolve_network_identity_from_vm_async = AsyncMock(
        return_value={
            "private_ip": None,
            "public_ip": None,
            "subnet_id": None,
            "vnet_id": None,
            "nic_id": None,
            "nic_name": None,
        }
    )

    request = MagicMock()
    request.resource_ids = ["vm-1", "vm-2"]
    request.metadata = {"resource_group": "test-rg"}

    result = await handler.check_hosts_status_async(request)

    assert [entry["instance_id"] for entry in result] == ["vm-1"]
    logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_status_strict_mode_raises_when_one_vm_lookup_fails():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    vm = MagicMock()
    vm.name = "vm-1"
    vm.vm_id = "vm-guid-1"
    vm.instance_view.statuses = []
    vm.hardware_profile.vm_size = "Standard_D4s_v5"
    vm.location = "eastus2"
    vm.zones = ["1"]

    async_compute = MagicMock()
    async_compute.virtual_machines.get = AsyncMock(side_effect=[vm, RuntimeError("boom")])
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute)
    handler._resolve_vm_names_async = AsyncMock(return_value=["vm-1", "vm-2"])
    azure_client.resolve_network_identity_from_vm_async = AsyncMock(
        return_value={
            "private_ip": None,
            "public_ip": None,
            "subnet_id": None,
            "vnet_id": None,
            "nic_id": None,
            "nic_name": None,
        }
    )

    request = MagicMock()
    request.resource_ids = ["vm-1", "vm-2"]
    request.metadata = {
        "resource_group": "test-rg",
        RAISE_ON_STATUS_ERROR_METADATA_KEY: True,
    }

    with pytest.raises(RuntimeError, match="Failed to get status for VM 'vm-2'"):
        await handler.check_hosts_status_async(request)


@pytest.mark.asyncio
async def test_status_best_effort_raises_when_all_vm_lookups_fail():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    async_compute = MagicMock()
    async_compute.virtual_machines.get = AsyncMock(side_effect=RuntimeError("boom"))
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute)
    handler._resolve_vm_names_async = AsyncMock(return_value=["vm-1"])

    request = MagicMock()
    request.resource_ids = ["vm-1"]
    request.metadata = {"resource_group": "test-rg"}

    with pytest.raises(RuntimeError, match="Failed to get status for VM 'vm-1'"):
        await handler.check_hosts_status_async(request)


@pytest.mark.asyncio
async def test_release_hosts_async_submits_deletions_for_resolved_vm_names():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)
    async_compute = MagicMock()
    async_compute.virtual_machines.begin_delete = AsyncMock()
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute)
    handler._resolve_vm_names_async = AsyncMock(return_value=["vm-1"])

    result = await handler.release_hosts_async(
        machine_ids=["vm-1"],
        resource_id="ignored",
        context=AzureReleaseContext(resource_group="test-rg"),
    )

    assert result is not None
    assert result["provider_data"]["operation_status"] == "submitted"
    async_compute.virtual_machines.begin_delete.assert_awaited_once()


def test_status_uses_direct_vm_name_lookup_without_listing_resource_group():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    vm = MagicMock()
    vm.name = "vm-1"
    vm.vm_id = "vm-guid-1"
    vm.instance_view.statuses = []
    vm.hardware_profile.vm_size = "Standard_D4s_v5"
    vm.location = "eastus2"
    vm.zones = ["1"]
    azure_client.resolve_network_identity_from_vm_async = AsyncMock(
        return_value={
        "private_ip": "10.0.0.4",
        "public_ip": None,
        "subnet_id": "/subscriptions/sub/.../subnets/default",
        "vnet_id": "/subscriptions/sub/.../virtualNetworks/test-vnet",
        "nic_id": "nic-id",
        "nic_name": "nic-vm-1",
        }
    )
    azure_client.compute_client.virtual_machines.get.return_value = vm

    request = MagicMock()
    request.resource_ids = ["vm-1"]
    request.metadata = {"resource_group": "test-rg"}

    result = run_operation(handler.check_hosts_status_async(request))

    assert result[0]["instance_id"] == "vm-1"
    azure_client.compute_client.virtual_machines.list.assert_not_called()


def test_release_returns_submitted_delete_metadata():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    vm_1 = MagicMock()
    vm_1.name = "vm-1"
    vm_1.vm_id = "guid-1"
    vm_2 = MagicMock()
    vm_2.name = "vm-2"
    vm_2.vm_id = "guid-2"
    azure_client.compute_client.virtual_machines.list.return_value = [vm_1, vm_2]
    azure_client.compute_client.virtual_machines.get.side_effect = ResourceNotFoundError("NotFound")
    azure_client.compute_client.virtual_machines.begin_delete.return_value = MagicMock()

    result = run_operation(
        handler.release_hosts_async(
            machine_ids=["guid-1", "guid-2"],
            resource_id="unused",
            context=AzureReleaseContext(resource_group="test-rg"),
        )
    )

    assert result["provider_data"]["operation_status"] == "submitted"
    assert result["provider_data"]["submitted_deletions"] == [
        {"requested_id": "guid-1", "vm_name": "vm-1"},
        {"requested_id": "guid-2", "vm_name": "vm-2"},
    ]
    assert _deleted_vm_names(azure_client) == ["vm-1", "vm-2"]


def test_release_uses_direct_vm_name_lookup_without_listing_resource_group():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    vm = MagicMock()
    vm.name = "vm-1"
    azure_client.compute_client.virtual_machines.get.return_value = vm
    azure_client.compute_client.virtual_machines.begin_delete.return_value = MagicMock()

    result = run_operation(
        handler.release_hosts_async(
            machine_ids=["vm-1"],
            resource_id="unused",
            context=AzureReleaseContext(resource_group="test-rg"),
        )
    )

    assert result["provider_data"]["submitted_deletions"] == [
        {"requested_id": "vm-1", "vm_name": "vm-1"},
    ]
    azure_client.compute_client.virtual_machines.list.assert_not_called()


def test_release_attempts_all_deletes_before_raising_aggregated_failure():
    azure_client = _make_azure_client()
    logger = MagicMock()
    handler = SingleVMHandler(azure_client=azure_client, logger=logger)

    vm_1 = MagicMock()
    vm_1.name = "vm-1"
    vm_1.vm_id = "guid-1"
    vm_2 = MagicMock()
    vm_2.name = "vm-2"
    vm_2.vm_id = "guid-2"
    vm_3 = MagicMock()
    vm_3.name = "vm-3"
    vm_3.vm_id = "guid-3"
    azure_client.compute_client.virtual_machines.list.return_value = [vm_1, vm_2, vm_3]
    azure_client.compute_client.virtual_machines.get.side_effect = ResourceNotFoundError("NotFound")

    def _begin_delete(*, resource_group_name, vm_name):
        if vm_name == "vm-2":
            raise RuntimeError("delete blocked")
        return MagicMock()

    azure_client.compute_client.virtual_machines.begin_delete.side_effect = _begin_delete

    with pytest.raises(TerminationError) as exc_info:
        run_operation(
            handler.release_hosts_async(
                machine_ids=["guid-1", "guid-2", "guid-3"],
                resource_id="unused",
                context=AzureReleaseContext(resource_group="test-rg"),
            )
        )

    exc = exc_info.value
    assert exc.resource_ids == ["guid-2"]
    assert exc.details["submitted_deletions"] == [
        {"requested_id": "guid-1", "vm_name": "vm-1"},
        {"requested_id": "guid-3", "vm_name": "vm-3"},
    ]
    assert exc.details["failed_deletions"] == [
        {"requested_id": "guid-2", "vm_name": "vm-2", "error": "delete blocked"},
    ]
    assert _deleted_vm_names(azure_client) == ["vm-1", "vm-2", "vm-3"]


@pytest.mark.asyncio
async def test_resolve_vm_names_async_maps_vm_ids_via_resource_group_listing():
    azure_client = _make_azure_client()
    handler = SingleVMHandler(azure_client=azure_client, logger=MagicMock())

    vm_1 = MagicMock()
    vm_1.name = "vm-1"
    vm_1.vm_id = "11111111-1111-1111-1111-111111111111"

    async_compute = MagicMock()
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute)
    async_compute.virtual_machines.get = AsyncMock(
        side_effect=ResourceNotFoundError("NotFound")
    )

    async_compute.virtual_machines.list.return_value = AsyncPager([vm_1])

    resolved = await handler._resolve_vm_names_async(
        "test-rg",
        ["11111111-1111-1111-1111-111111111111"],
    )

    assert resolved == ["vm-1"]


@pytest.mark.asyncio
async def test_resolve_vm_names_async_preserves_input_order_for_mixed_vm_names_and_ids():
    azure_client = _make_azure_client()
    handler = SingleVMHandler(azure_client=azure_client, logger=MagicMock())

    vm_1 = MagicMock()
    vm_1.name = "vm-1"
    vm_1.vm_id = "11111111-1111-1111-1111-111111111111"

    def _get_vm(*, resource_group_name, vm_name):
        if resource_group_name == "test-rg" and vm_name == "vm-2":
            vm = MagicMock()
            vm.name = "vm-2"
            return vm
        raise ResourceNotFoundError("NotFound")

    async_compute = MagicMock()
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute)
    async_compute.virtual_machines.get = AsyncMock(side_effect=_get_vm)

    async_compute.virtual_machines.list.return_value = AsyncPager([vm_1])

    resolved = await handler._resolve_vm_names_async(
        "test-rg",
        ["vm-2", "11111111-1111-1111-1111-111111111111"],
    )

    assert resolved == ["vm-2", "vm-1"]


@pytest.mark.asyncio
async def test_resolve_vm_names_async_maps_vm_ids_via_resource_group_listing_against_async_client():
    azure_client = _make_azure_client()
    handler = SingleVMHandler(azure_client=azure_client, logger=MagicMock())

    vm_1 = MagicMock()
    vm_1.name = "vm-1"
    vm_1.vm_id = "11111111-1111-1111-1111-111111111111"

    async_compute = MagicMock()
    async_compute.virtual_machines.get = AsyncMock(
        side_effect=ResourceNotFoundError("NotFound")
    )

    async_compute.virtual_machines.list.return_value = AsyncPager([vm_1])
    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute)

    resolved = await handler._resolve_vm_names_async(
        "test-rg",
        ["11111111-1111-1111-1111-111111111111"],
    )

    assert resolved == ["vm-1"]
