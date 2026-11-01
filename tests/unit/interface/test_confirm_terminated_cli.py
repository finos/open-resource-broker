"""`orb requests confirm-terminated`: parsing, operator label, exit semantics."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.orchestration.dtos import ConfirmTerminatedOutput
from orb.cli.args import add_request_actions
from orb.domain.base.exceptions import ValidationError
from orb.domain.request.termination_confirmation import InstanceStillVisibleError
from orb.interface.request_command_handlers import handle_confirm_terminated


def _args(**overrides):
    values = {
        "request_id": "ret-1",
        "flag_request_id": None,
        "machine_ids": ["i-1"],
        "reason": "verified",
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
    for argv in (
        ["confirm-terminated", "ret-1", "--reason", "gone"],
        ["confirm-terminated", "ret-1", "-m", "i-1"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
