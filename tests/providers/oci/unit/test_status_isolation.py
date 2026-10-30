"""Per-machine status failures must not corrupt stored machine state.

The handler runs behind MachineSyncService, so these tests drive both together:
a machine OCI reports as 404 is omitted for an acquire request, while a 404 on
a return request and any other per-machine failure fail the whole status call
and leave stored machines untouched.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.application.services.machine_sync_service import MachineSyncService
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType
from orb.providers.base.strategy import ProviderOperationType
from orb.providers.oci.cli_client import OCICommandError
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler

FIRST = "ocid1.instance.oc1.us-phoenix-1.aaaa"
SECOND = "ocid1.instance.oc1.us-phoenix-1.bbbb"


class _RealHandlerRegistry:
    """Registry stand-in that routes status operations to the real OCI handler."""

    def __init__(self, handler: OCIComputeHandler) -> None:
        self._handler = handler

    async def execute_operation(self, provider_name, operation):
        if operation.operation_type == ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES:
            return await self._handler.describe_resource_instances(operation)
        return self._handler.get_instance_status(operation)


def _oci_error(status: int, code: str | None = "NotAuthorizedOrNotFound") -> OCICommandError:
    return OCICommandError("failed", status=status, code=code)


def _handler(outcomes: dict) -> OCIComputeHandler:
    """Build a handler whose instance-get calls return or raise per instance ID."""
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1")
    handler._oci_cli_available = True

    def run(args, *unused, **unused_kwargs):
        if args[:3] == ["compute", "instance", "list-vnics"]:
            outcome = outcomes.get(("vnics", args[args.index("--instance-id") + 1]))
            if isinstance(outcome, Exception):
                raise outcome
            return {"data": []}
        outcome = outcomes[args[args.index("--instance-id") + 1]]
        if isinstance(outcome, Exception):
            raise outcome
        return {
            "data": {
                "lifecycle-state": "RUNNING",
                "shape": "VM.Standard.E5.Flex",
                "image-id": "ocid1.image.oc1.us-phoenix-1.img",
            }
        }

    handler._run_oci = run  # type: ignore[method-assign]
    return handler


def _service(handler: OCIComputeHandler) -> MachineSyncService:
    return MachineSyncService(
        command_bus=MagicMock(),
        uow_factory=MagicMock(),
        config_port=MagicMock(),
        logger=MagicMock(),
        provider_registry_service=_RealHandlerRegistry(handler),  # type: ignore[arg-type]
    )


def _request(request_type: RequestType) -> Request:
    request = Request.create_new_request(
        request_type=request_type,
        template_id="tpl-1",
        machine_count=2,
        provider_type="oci",
        provider_name="oci_default_us-phoenix-1",
        provider_api="OCICompute",
    )
    if request_type == RequestType.RETURN:
        request.machine_ids = [FIRST, SECOND]
    else:
        request.resource_ids = [FIRST, SECOND]
    return request


def _stored(service: MachineSyncService, request: Request) -> list:
    return [
        service._create_machine_from_processed_data(
            {"instance_id": machine_id, "status": "running", "instance_type": "shape"},
            request,
        )
        for machine_id in (FIRST, SECOND)
    ]


@pytest.mark.asyncio
async def test_not_found_machine_is_omitted_and_not_marked_terminated():
    service = _service(_handler({FIRST: _oci_error(404), SECOND: None}))
    request = _request(RequestType.ACQUIRE)
    stored = _stored(service, request)

    machines, _ = await service.fetch_provider_machines(request, stored)

    assert [m.machine_id.value for m in machines] == [SECOND]
    assert all(m.status != MachineStatus.TERMINATED for m in machines)


@pytest.mark.parametrize(
    "outcomes",
    [
        {FIRST: _oci_error(429), SECOND: None},
        {FIRST: None, SECOND: _oci_error(500)},
        {FIRST: None, SECOND: None, ("vnics", FIRST): _oci_error(500)},
        {FIRST: None, SECOND: None, ("vnics", SECOND): _oci_error(404)},
    ],
)
@pytest.mark.asyncio
async def test_other_machine_error_fails_call_and_keeps_stored_machines(outcomes):
    service = _service(_handler(outcomes))
    request = _request(RequestType.ACQUIRE)
    stored = _stored(service, request)
    before = [(m.machine_id.value, m.status, m.version) for m in stored]

    machines, _ = await service.fetch_provider_machines(request, stored)

    assert machines is stored
    assert [(m.machine_id.value, m.status, m.version) for m in machines] == before


@pytest.mark.asyncio
async def test_return_request_error_marks_nothing_terminated():
    service = _service(_handler({FIRST: _oci_error(429), SECOND: None}))
    request = _request(RequestType.RETURN)
    stored = _stored(service, request)

    machines, _ = await service.fetch_provider_machines(request, stored)

    assert machines is stored
    assert all(m.status == MachineStatus.RUNNING for m in machines)


@pytest.mark.parametrize("code", ["NotAuthorizedOrNotFound", "NotFound", None])
@pytest.mark.asyncio
async def test_return_request_not_found_machine_is_never_marked_terminated(code):
    service = _service(_handler({FIRST: _oci_error(404, code), SECOND: None}))
    request = _request(RequestType.RETURN)
    stored = _stored(service, request)
    for machine in stored:
        machine.status = MachineStatus.SHUTTING_DOWN
    before = [(m.machine_id.value, m.status, m.version) for m in stored]

    machines, _ = await service.fetch_provider_machines(request, stored)

    assert machines is stored
    assert [(m.machine_id.value, m.status, m.version) for m in machines] == before
    assert all(m.status != MachineStatus.TERMINATED for m in machines)


def test_no_machine_is_reported_with_unknown_status_on_failure():
    handler = _handler({FIRST: _oci_error(500), SECOND: None})
    operation = SimpleNamespace(
        operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
        parameters={"instance_ids": [FIRST, SECOND]},
        context={},
    )

    result = handler.get_instance_status(operation)  # type: ignore[arg-type]

    assert not result.success
    assert result.error_code == "OCI_STATUS_FAILED"
    assert not (result.data or {}).get("instances")
