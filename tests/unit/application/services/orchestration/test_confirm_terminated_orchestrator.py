"""ConfirmTerminatedOrchestrator: validation order, provider verification, idempotency."""

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
    TerminationNotVerifiedError,
)


def _request(metadata=None):
    return Request.create_return_request(
        machine_ids=["i-1", "i-2"],
        provider_type="acme",
        provider_name="acme-main",
        provider_api="AcmeCompute",
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
            {"machine_id": m, "verification": command.verifications.get(m)}
            for m in command.newly_confirmed
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


def _input(request, ids=("i-1",), reason="verified", force=False):
    return ConfirmTerminatedInput(
        request_id=str(request.request_id),
        machine_ids=list(ids),
        reason=reason,
        operator="alice",
        role="operator",
        force=force,
    )


def _probe(instances=None, success=True):
    return SimpleNamespace(
        success=success,
        data={"instances": instances or []},
        error_code=None if success else "X",
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["gone", "terminated", "TERMINATED"])
async def test_provider_reported_gone_is_verified(status):
    request = _request()
    orchestrator, bus, _ = _orchestrator(
        request, _probe([{"instance_id": "i-1", "status": status}])
    )
    out = await orchestrator.execute(_input(request))
    assert out.confirmed == ["i-1"]
    assert out.warnings == []
    assert bus.execute.await_args.args[0].verifications == {"i-1": "verified"}


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("status", ["visible", "running", "pending"])
async def test_still_present_instance_is_refused_even_with_force(status, force):
    request = _request()
    orchestrator, bus, _ = _orchestrator(
        request, _probe([{"instance_id": "i-1", "status": status}])
    )
    with pytest.raises(InstanceStillVisibleError, match="i-1") as caught:
        await orchestrator.execute(_input(request, force=force))
    assert caught.value.details["machine_ids"] == ["i-1"]
    bus.execute.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["error_result", "exception", "machine_omitted", "unknown_status"]
)
async def test_unknown_without_force_is_refused_and_nothing_written(scenario):
    request = _request()
    if scenario == "error_result":
        orchestrator, bus, _ = _orchestrator(request, _probe(success=False))
    elif scenario == "exception":
        orchestrator, bus, _ = _orchestrator(request, probe_raises=RuntimeError("boom"))
    elif scenario == "machine_omitted":
        orchestrator, bus, _ = _orchestrator(request, _probe([]))
    else:
        orchestrator, bus, _ = _orchestrator(
            request, _probe([{"instance_id": "i-1", "status": "unknown"}])
        )
    with pytest.raises(TerminationNotVerifiedError, match="i-1"):
        await orchestrator.execute(_input(request))
    bus.execute.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["error_result", "exception", "machine_omitted"])
async def test_unknown_with_force_is_recorded_as_operator_attested(scenario):
    request = _request()
    if scenario == "error_result":
        orchestrator, bus, _ = _orchestrator(request, _probe(success=False))
    elif scenario == "exception":
        orchestrator, bus, _ = _orchestrator(request, probe_raises=RuntimeError("boom"))
    else:
        orchestrator, bus, _ = _orchestrator(request, _probe([]))
    out = await orchestrator.execute(_input(request, force=True))
    assert out.confirmed == ["i-1"]
    assert out.warnings
    assert bus.execute.await_args.args[0].verifications == {"i-1": "operator_attested"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mixed_result_attests_only_what_the_provider_could_not_verify():
    request = _request()
    orchestrator, bus, _ = _orchestrator(
        request, _probe([{"instance_id": "i-1", "status": "gone"}])
    )
    with pytest.raises(TerminationNotVerifiedError, match="i-2"):
        await orchestrator.execute(_input(request, ["i-1", "i-2"]))
    out = await orchestrator.execute(_input(request, ["i-1", "i-2"], force=True))
    assert out.confirmed == ["i-1", "i-2"]
    assert bus.execute.await_args.args[0].verifications == {
        "i-1": "verified",
        "i-2": "operator_attested",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_one_present_machine_refuses_the_whole_batch():
    request = _request()
    orchestrator, bus, _ = _orchestrator(
        request,
        _probe(
            [
                {"instance_id": "i-1", "status": "gone"},
                {"instance_id": "i-2", "status": "running"},
            ]
        ),
    )
    with pytest.raises(InstanceStillVisibleError, match="i-2"):
        await orchestrator.execute(_input(request, ["i-1", "i-2"], force=True))
    bus.execute.assert_not_awaited()


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
    orchestrator, _, registry = _orchestrator(
        request, _probe([{"instance_id": "i-2", "status": "gone"}])
    )
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
