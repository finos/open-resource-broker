"""ConfirmTerminatedOrchestrator: validation order, live probe, idempotency."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.orchestration.confirm_terminated import (
    ConfirmTerminatedOrchestrator,
)
from orb.application.services.orchestration.dtos import ConfirmTerminatedInput
from orb.domain.base.exceptions import EntityNotFoundError, ValidationError
from orb.domain.request.aggregate import Request
from orb.domain.request.termination_confirmation import (
    CONFIRMED_TERMINATED_KEY,
    InstanceStillVisibleError,
)


def _request(metadata=None):
    return Request.create_return_request(
        machine_ids=["i-1", "i-2"],
        provider_type="oci",
        provider_name="oci-main",
        provider_api="OCICompute",
        metadata=metadata,
    )


def _orchestrator(request, probe_result=None, probe_raises=None):
    uow = MagicMock()
    uow.requests.find_by_id.return_value = request

    @contextmanager
    def create():
        yield uow

    factory = MagicMock()
    factory.create_unit_of_work.side_effect = create

    async def execute(command):
        command.newly_confirmed = list(command.machine_ids)
        command.records = [
            {"machine_id": m, "verification": command.verification} for m in command.newly_confirmed
        ]

    bus = MagicMock()
    bus.execute = AsyncMock(side_effect=execute)
    registry = MagicMock()
    if probe_raises:
        registry.execute_operation = AsyncMock(side_effect=probe_raises)
    else:
        registry.execute_operation = AsyncMock(return_value=probe_result)
    orchestrator = ConfirmTerminatedOrchestrator(
        command_bus=bus,
        uow_factory=factory,
        provider_registry_service=registry,
        logger=MagicMock(),
    )
    return orchestrator, bus, registry


def _input(request, ids=("i-1",), reason="verified"):
    return ConfirmTerminatedInput(
        request_id=str(request.request_id),
        machine_ids=list(ids),
        reason=reason,
        operator="alice",
        role="operator",
    )


def _probe(instances=None, success=True):
    return SimpleNamespace(
        success=success,
        data={"instances": instances or []},
        error_code=None if success else "X",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_not_visible_is_accepted_without_warning():
    request = _request()
    orchestrator, bus, _ = _orchestrator(request, _probe([]))
    out = await orchestrator.execute(_input(request))
    assert out.confirmed == ["i-1"]
    assert out.warnings == []
    assert bus.execute.await_args.args[0].verification == "not_visible"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_still_running_instance_is_rejected_and_nothing_written():
    request = _request()
    orchestrator, bus, _ = _orchestrator(
        request, _probe([{"instance_id": "i-1", "status": "visible"}])
    )
    with pytest.raises(InstanceStillVisibleError, match="i-1"):
        await orchestrator.execute(_input(request))
    bus.execute.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_visible_but_terminated_is_accepted():
    request = _request()
    orchestrator, bus, _ = _orchestrator(
        request, _probe([{"instance_id": "i-1", "status": "terminated"}])
    )
    out = await orchestrator.execute(_input(request))
    assert out.confirmed == ["i-1"]
    assert bus.execute.await_args.args[0].verification == "provider_reports_terminated"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error_result", "exception"])
async def test_unavailable_probe_accepts_with_warning(failure):
    request = _request()
    if failure == "error_result":
        orchestrator, bus, _ = _orchestrator(request, _probe(success=False))
    else:
        orchestrator, bus, _ = _orchestrator(request, probe_raises=RuntimeError("boom"))
    out = await orchestrator.execute(_input(request))
    assert out.confirmed == ["i-1"]
    assert out.warnings
    assert bus.execute.await_args.args[0].verification == "probe_unavailable"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_already_confirmed_skips_probe_and_write():
    request = _request(
        {CONFIRMED_TERMINATED_KEY: [{"machine_id": "i-1", "operator": "bob", "reason": "r"}]}
    )
    orchestrator, bus, registry = _orchestrator(request, _probe([]))
    out = await orchestrator.execute(_input(request))
    assert out.already_confirmed == ["i-1"]
    assert out.confirmed == []
    assert out.records[0]["operator"] == "bob"
    registry.execute_operation.assert_not_awaited()
    bus.execute.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_probe_only_asks_about_unconfirmed_machines():
    request = _request({CONFIRMED_TERMINATED_KEY: [{"machine_id": "i-1"}]})
    orchestrator, _, registry = _orchestrator(request, _probe([]))
    await orchestrator.execute(_input(request, ["i-1", "i-2"]))
    operation = registry.execute_operation.await_args.args[1]
    assert operation.parameters["instance_ids"] == ["i-2"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ids", "reason"),
    [([], "r"), (["i-1"], ""), (["i-1"], "x" * 501), (["i-9"], "r")],
)
async def test_invalid_input_never_probes_or_writes(ids, reason):
    request = _request()
    orchestrator, bus, registry = _orchestrator(request, _probe([]))
    with pytest.raises(ValidationError):
        await orchestrator.execute(_input(request, ids, reason))
    registry.execute_operation.assert_not_awaited()
    bus.execute.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_request_is_not_found():
    request = _request()
    orchestrator, _, _ = _orchestrator(None, _probe([]))
    with pytest.raises(EntityNotFoundError):
        await orchestrator.execute(_input(request))
