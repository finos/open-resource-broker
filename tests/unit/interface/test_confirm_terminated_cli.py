"""`orb requests confirm-terminated`: parsing, operator label, exit semantics."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.orchestration.dtos import ConfirmTerminatedOutput
from orb.cli.args import add_request_actions
from orb.domain.base.exceptions import ValidationError
from orb.domain.request.termination_confirmation import (
    InstanceStillVisibleError,
    TerminationNotVerifiedError,
)
from orb.interface.request_command_handlers import handle_confirm_terminated


def _args(**overrides):
    values = {
        "request_id": "ret-1",
        "flag_request_id": None,
        "machine_ids": ["i-1"],
        "reason": "verified",
        "force": False,
    }
    values.update(overrides)
    args = argparse.Namespace(**values)
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(
        return_value=ConfirmTerminatedOutput(request_id="ret-1", confirmed=["i-1"])
    )
    args._container = MagicMock()
    args._container.get.return_value = orchestrator
    return args, orchestrator


@pytest.mark.unit
@pytest.mark.asyncio
async def test_operator_is_recorded_as_advisory_cli_label(monkeypatch):
    monkeypatch.setattr("getpass.getuser", lambda: "alice")
    monkeypatch.setattr("socket.gethostname", lambda: "build-host")
    args, orchestrator = _args()
    response = await handle_confirm_terminated(args)
    sent = orchestrator.execute.await_args.args[0]
    assert sent.operator == "cli:alice@build-host"
    assert sent.machine_ids == ["i-1"]
    assert sent.reason == "verified"
    assert isinstance(response, InterfaceResponse)
    assert response.exit_code == 0
    assert response.data["confirmed"] == ["i-1"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_still_visible_instance_is_an_error_result():
    args, orchestrator = _args()
    orchestrator.execute.side_effect = InstanceStillVisibleError("still shown")
    result = await handle_confirm_terminated(args)
    assert isinstance(result, dict)
    assert result["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unverified_instance_is_an_error_result():
    args, orchestrator = _args()
    orchestrator.execute.side_effect = TerminationNotVerifiedError("cannot verify")
    result = await handle_confirm_terminated(args)
    assert isinstance(result, dict)
    assert result["error"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
async def test_force_flag_reaches_the_orchestrator(force):
    args, orchestrator = _args(force=force)
    await handle_confirm_terminated(args)
    assert orchestrator.execute.await_args.args[0].force is force


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_request_id_is_an_error_result():
    args, orchestrator = _args(request_id=None)
    result = await handle_confirm_terminated(args)
    assert result["error"]
    orchestrator.execute.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_validation_errors_propagate_for_nonzero_exit():
    args, orchestrator = _args()
    orchestrator.execute.side_effect = ValidationError("Machines not part of return request")
    with pytest.raises(Exception, match="not part of"):
        await handle_confirm_terminated(args)


@pytest.mark.unit
def test_parser_requires_machine_id_and_reason():
    parser = argparse.ArgumentParser()
    add_request_actions(parser.add_subparsers(dest="action"))
    ns = parser.parse_args(
        ["confirm-terminated", "ret-1", "-m", "i-1", "--machine-id", "i-2", "--reason", "gone"]
    )
    assert ns.machine_ids == ["i-1", "i-2"]
    assert ns.reason == "gone"
    assert ns.force is False
    forced = parser.parse_args(
        ["confirm-terminated", "ret-1", "-m", "i-1", "--reason", "gone", "--force"]
    )
    assert forced.force is True
    for argv in (
        ["confirm-terminated", "ret-1", "--reason", "gone"],
        ["confirm-terminated", "ret-1", "-m", "i-1"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


def _acme_args(probe_instances=None, probe_success=True, force=False):
    """Real orchestrator over a provider that is not OCI; only the provider is faked."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from orb.application.services.orchestration.confirm_terminated import (
        ConfirmTerminatedOrchestrator,
    )
    from orb.domain.request.aggregate import Request

    request = Request.create_return_request(
        machine_ids=["i-1"],
        provider_type="acme",
        provider_name="acme-main",
        provider_api="AcmeCompute",
    )
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
            {"machine_id": m, "verification": command.verifications[m]} for m in command.machine_ids
        ]

    bus = MagicMock()
    bus.execute = AsyncMock(side_effect=execute)
    registry = MagicMock()
    registry.execute_operation = AsyncMock(
        return_value=SimpleNamespace(
            success=probe_success,
            data={"instances": probe_instances or []},
            error_code=None if probe_success else "PROBE_FAILED",
        )
    )
    orchestrator = ConfirmTerminatedOrchestrator(
        command_bus=bus,
        uow_factory=factory,
        provider_registry_service=registry,
        logger=MagicMock(),
    )
    args = argparse.Namespace(
        request_id=str(request.request_id),
        flag_request_id=None,
        machine_ids=["i-1"],
        reason="verified",
        force=force,
    )
    args._container = MagicMock()
    args._container.get.return_value = orchestrator
    return args, bus


@pytest.mark.unit
@pytest.mark.asyncio
class TestNonOciProvider:
    async def test_provider_verified_gone_is_recorded_verified(self):
        args, _ = _acme_args([{"instance_id": "i-1", "status": "gone"}])
        response = await handle_confirm_terminated(args)
        assert response.exit_code == 0
        assert response.data["records"][0]["verification"] == "verified"

    async def test_still_present_is_an_error_even_with_force(self):
        args, bus = _acme_args([{"instance_id": "i-1", "status": "running"}], force=True)
        result = await handle_confirm_terminated(args)
        assert isinstance(result, dict)
        assert result["error"] == "Instance still visible"
        bus.execute.assert_not_awaited()

    async def test_unknown_without_force_is_an_error(self):
        args, bus = _acme_args(probe_success=False)
        result = await handle_confirm_terminated(args)
        assert isinstance(result, dict)
        assert result["error"] == "Termination not verified"
        bus.execute.assert_not_awaited()

    async def test_unknown_with_force_is_recorded_operator_attested(self):
        args, _ = _acme_args(probe_success=False, force=True)
        response = await handle_confirm_terminated(args)
        assert response.exit_code == 0
        assert response.data["records"][0]["verification"] == "operator_attested"
        assert response.data["warnings"]
