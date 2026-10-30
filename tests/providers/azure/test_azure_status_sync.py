"""Azure status lookups through MachineSyncService.

A VM or scale set Azure reports as missing (404) is omitted so the machine can
be recorded as gone. Any other failure fails the whole status call so stored
machine state is kept and the poll is retried.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.mgmt.compute.models import OrchestrationMode

from orb.application.services.machine_sync_service import MachineSyncService
from orb.domain.base.value_objects import InstanceType
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.machine_status import MachineStatus
from orb.providers.azure.infrastructure.handlers.single_vm_handler import SingleVMHandler
from orb.providers.azure.infrastructure.handlers.vmss_handler import VMSSHandler
from orb.providers.base.strategy import ProviderResult
from tests.providers.azure.strategy_test_support import (
    make_single_vm_azure_client,
    make_vmss_azure_client,
)

_NETWORK = {
    "private_ip": None,
    "public_ip": None,
    "subnet_id": None,
    "vnet_id": None,
    "nic_id": None,
    "nic_name": None,
}


def _not_found() -> Exception:
    return ResourceNotFoundError(message="ResourceNotFound")


def _throttled() -> Exception:
    response = MagicMock()
    response.status_code = 429
    response.reason = "Too Many Requests"
    response.text.return_value = ""
    return HttpResponseError(message="TooManyRequests", response=response)


def _bare_404() -> Exception:
    """A 404 response that carries no Azure error code."""
    response = MagicMock()
    response.status_code = 404
    response.reason = "Not Found"
    response.text.return_value = ""
    return HttpResponseError(message="not here", response=response)


def _vm(name: str, instance_id: str | None = None) -> MagicMock:
    vm = MagicMock()
    vm.name = name
    vm.vm_id = f"guid-{name}"
    if instance_id is not None:
        vm.instance_id = instance_id
    vm.instance_view.statuses = []
    vm.hardware_profile.vm_size = "Standard_D4s_v5"
    vm.location = "eastus2"
    vm.zones = []
    vm.tags = {}
    return vm


def _machine(machine_id: str, resource_id: str) -> Machine:
    return Machine(
        machine_id=MachineId(value=machine_id),
        template_id="tmpl",
        provider_type="azure",
        provider_name="azure-default",
        provider_api="SingleVM",
        resource_id=resource_id,
        instance_type=InstanceType(value="Standard_D4s_v5"),
        image_id="img",
        status=MachineStatus.RUNNING,
        request_id="req-1",
    )


def _request(request_type: str, resource_ids: list[str], machine_ids: list[str]) -> MagicMock:
    request = MagicMock()
    request.request_id = "req-1"
    request.request_type.value = request_type
    request.provider_name = "azure-default"
    request.provider_type = "azure"
    request.provider_api = "SingleVM"
    request.template_id = "tmpl"
    request.resource_ids = resource_ids
    request.machine_ids = machine_ids
    request.requested_count = len(machine_ids)
    request.metadata = {"resource_group": "rg"}
    return request


class _StubRegistry:
    """Mirror the strategy contract over a real handler.

    A successful handler call becomes ``instances``; an exception becomes a
    failed ProviderResult, which is how the strategy reports it.
    """

    def __init__(self, handler: object) -> None:
        self._handler = handler

    async def execute_operation(self, provider_name: str, operation: object) -> ProviderResult:
        request = operation.parameters["request"]  # type: ignore[attr-defined]
        try:
            instances = await self._handler.check_hosts_status_async(request)  # type: ignore[attr-defined]
        except Exception as exc:
            return ProviderResult.error_result(str(exc), "STATUS_ERROR")
        return ProviderResult.success_result({"instances": instances})


def _service(handler: object) -> MachineSyncService:
    service = MachineSyncService(
        command_bus=MagicMock(),
        uow_factory=MagicMock(),
        config_port=MagicMock(),
        logger=MagicMock(),
        provider_registry_service=_StubRegistry(handler),  # type: ignore[arg-type]
    )
    return service


def _single_vm_handler(outcomes: dict[str, object]) -> SingleVMHandler:
    azure_client = make_single_vm_azure_client()

    def _get(**kwargs: object) -> MagicMock:
        outcome = outcomes[str(kwargs["vm_name"])]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]

    azure_client.compute_client.virtual_machines.get.side_effect = _get
    azure_client.resolve_network_identity_from_vm_async = AsyncMock(return_value=_NETWORK)
    handler = SingleVMHandler(azure_client=azure_client, logger=MagicMock())
    handler._resolve_vm_names_async = AsyncMock(side_effect=lambda _rg, ids: list(ids))  # type: ignore[method-assign]
    return handler


def _vmss_handler(outcomes: dict[str, object]) -> VMSSHandler:
    azure_client = make_vmss_azure_client()
    sets = azure_client.compute_client.virtual_machine_scale_sets
    members = azure_client.compute_client.virtual_machine_scale_set_vms

    def _get(**kwargs: object) -> MagicMock:
        outcome = outcomes[str(kwargs["vm_scale_set_name"])]
        if isinstance(outcome, Exception):
            raise outcome
        vmss = MagicMock()
        vmss.orchestration_mode = OrchestrationMode.UNIFORM
        return vmss

    def _list(**kwargs: object) -> list[MagicMock]:
        return [outcomes[str(kwargs["virtual_machine_scale_set_name"])]]  # type: ignore[list-item]

    sets.get.side_effect = _get
    members.list.side_effect = _list
    azure_client.resolve_network_identity_from_vm_async = AsyncMock(return_value=_NETWORK)
    return VMSSHandler(azure_client=azure_client, logger=MagicMock())


@pytest.mark.unit
@pytest.mark.asyncio
class TestSingleVmStatusSync:
    async def test_missing_vm_is_omitted_and_not_terminated_on_acquire(self) -> None:
        handler = _single_vm_handler({"vm-1": _not_found(), "vm-2": _vm("vm-2")})
        service = _service(handler)
        db = [_machine("vm-1", "vm-1"), _machine("vm-2", "vm-2")]
        request = _request("acquire", ["vm-1", "vm-2"], [])

        machines, _ = await service.fetch_provider_machines(request, db)

        assert [str(m.machine_id.value) for m in machines] == ["vm-2"]
        assert all(m.status != MachineStatus.TERMINATED for m in machines)

    async def test_throttled_vm_fails_call_and_keeps_stored_state(self) -> None:
        handler = _single_vm_handler({"vm-1": _throttled(), "vm-2": _vm("vm-2")})
        service = _service(handler)
        db = [_machine("vm-1", "vm-1"), _machine("vm-2", "vm-2")]
        request = _request("acquire", ["vm-1", "vm-2"], [])

        machines, _ = await service.fetch_provider_machines(request, db)

        assert machines == db
        assert [m.status for m in machines] == [MachineStatus.RUNNING, MachineStatus.RUNNING]

    async def test_throttled_vm_on_return_request_terminates_nothing(self) -> None:
        handler = _single_vm_handler({"vm-1": _throttled(), "vm-2": _vm("vm-2")})
        service = _service(handler)
        db = [_machine("vm-1", "vm-1"), _machine("vm-2", "vm-2")]
        request = _request("return", ["vm-1", "vm-2"], ["vm-1", "vm-2"])

        machines, _ = await service.fetch_provider_machines(request, db)

        assert machines == db
        assert all(m.status != MachineStatus.TERMINATED for m in machines)

    async def test_all_vms_missing_returns_empty_instead_of_raising(self) -> None:
        handler = _single_vm_handler({"vm-1": _not_found(), "vm-2": _not_found()})
        service = _service(handler)
        db = [_machine("vm-1", "vm-1"), _machine("vm-2", "vm-2")]
        request = _request("acquire", ["vm-1", "vm-2"], [])

        instances = await handler.check_hosts_status_async(request)
        machines, _ = await service.fetch_provider_machines(request, db)

        assert instances == []
        assert machines == []

    async def test_all_vms_missing_on_return_request_marks_all_terminated(self) -> None:
        handler = _single_vm_handler({"vm-1": _not_found(), "vm-2": _not_found()})
        service = _service(handler)
        db = [_machine("vm-1", "vm-1"), _machine("vm-2", "vm-2")]
        request = _request("return", ["vm-1", "vm-2"], ["vm-1", "vm-2"])

        machines, _ = await service.fetch_provider_machines(request, db)

        assert sorted(str(m.machine_id.value) for m in machines) == ["vm-1", "vm-2"]
        assert all(m.status == MachineStatus.TERMINATED for m in machines)


@pytest.mark.unit
@pytest.mark.asyncio
class TestVmssStatusSync:
    async def test_missing_scale_set_is_omitted_and_not_terminated_on_acquire(self) -> None:
        handler = _vmss_handler({"vmss-a": _not_found(), "vmss-b": _vm("vmss-b_1", "1")})
        service = _service(handler)
        db = [_machine("a-1", "vmss-a"), _machine("1", "vmss-b")]
        request = _request("acquire", ["vmss-a", "vmss-b"], [])

        machines, _ = await service.fetch_provider_machines(request, db)

        assert [str(m.machine_id.value) for m in machines] == ["1"]
        assert all(m.status != MachineStatus.TERMINATED for m in machines)

    async def test_throttled_scale_set_fails_call_and_keeps_stored_state(self) -> None:
        handler = _vmss_handler({"vmss-a": _throttled(), "vmss-b": _vm("vmss-b_1", "1")})
        service = _service(handler)
        db = [_machine("a-1", "vmss-a"), _machine("1", "vmss-b")]
        request = _request("acquire", ["vmss-a", "vmss-b"], [])

        machines, _ = await service.fetch_provider_machines(request, db)

        assert machines == db

    async def test_throttled_scale_set_on_return_request_terminates_nothing(self) -> None:
        handler = _vmss_handler({"vmss-a": _throttled(), "vmss-b": _vm("vmss-b_1", "1")})
        service = _service(handler)
        db = [_machine("a-1", "vmss-a"), _machine("1", "vmss-b")]
        request = _request("return", ["vmss-a", "vmss-b"], ["a-1", "1"])

        machines, _ = await service.fetch_provider_machines(request, db)

        assert machines == db
        assert all(m.status != MachineStatus.TERMINATED for m in machines)

    async def test_all_scale_sets_missing_returns_empty_instead_of_raising(self) -> None:
        handler = _vmss_handler({"vmss-a": _not_found(), "vmss-b": _not_found()})
        service = _service(handler)
        db = [_machine("a-1", "vmss-a"), _machine("b-1", "vmss-b")]
        request = _request("acquire", ["vmss-a", "vmss-b"], [])

        instances = await handler.check_hosts_status_async(request)
        machines, _ = await service.fetch_provider_machines(request, db)

        assert instances == []
        assert machines == []

    async def test_all_scale_sets_missing_on_return_request_marks_all_terminated(self) -> None:
        handler = _vmss_handler({"vmss-a": _not_found(), "vmss-b": _not_found()})
        service = _service(handler)
        db = [_machine("a-1", "vmss-a"), _machine("b-1", "vmss-b")]
        request = _request("return", ["vmss-a", "vmss-b"], ["a-1", "b-1"])

        machines, _ = await service.fetch_provider_machines(request, db)

        assert all(m.status == MachineStatus.TERMINATED for m in machines)
        assert len(machines) == 2


def _flexible_vmss_handler(list_error: Exception) -> VMSSHandler:
    azure_client = make_vmss_azure_client()
    vmss = MagicMock()
    vmss.orchestration_mode = OrchestrationMode.FLEXIBLE
    azure_client.compute_client.virtual_machine_scale_sets.get.side_effect = lambda **_: vmss
    azure_client.compute_client.virtual_machines.list.side_effect = list_error
    azure_client.resolve_network_identity_from_vm_async = AsyncMock(return_value=_NETWORK)
    return VMSSHandler(azure_client=azure_client, logger=MagicMock())


@pytest.mark.unit
@pytest.mark.asyncio
class TestFlexibleVmssStatusSync:
    async def test_missing_scale_set_is_omitted(self) -> None:
        handler = _flexible_vmss_handler(_not_found())

        instances = await handler.check_hosts_status_async(_request("acquire", ["vmss-a"], []))

        assert instances == []

    async def test_throttled_listing_fails_the_call(self) -> None:
        handler = _flexible_vmss_handler(_throttled())

        with pytest.raises(HttpResponseError, match="TooManyRequests"):
            await handler._list_flexible_vmss_instances_async("rg", "vmss-a")
        with pytest.raises(RuntimeError, match="TooManyRequests"):
            await handler.check_hosts_status_async(_request("acquire", ["vmss-a"], []))

    async def test_not_found_listing_raises_vmss_not_found(self) -> None:
        from orb.providers.azure.exceptions.azure_exceptions import VMSSNotFoundError

        handler = _flexible_vmss_handler(_not_found())

        with pytest.raises(VMSSNotFoundError):
            await handler._list_flexible_vmss_instances_async("rg", "vmss-a")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_not_found(), True),
        (_bare_404(), True),
        (_throttled(), False),
        (RuntimeError("boom"), False),
        (TimeoutError("timed out"), False),
    ],
)
def test_is_azure_not_found_matches_only_genuine_404(exc: Exception, expected: bool) -> None:
    from orb.providers.azure.infrastructure.error_utils import is_azure_not_found

    assert is_azure_not_found(exc) is expected


@pytest.mark.unit
def test_is_azure_not_found_follows_the_cause_chain() -> None:
    from orb.providers.azure.infrastructure.error_utils import is_azure_not_found

    try:
        try:
            raise _not_found()
        except Exception as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert is_azure_not_found(outer) is True
