"""``orb machines return --force`` reaches the return command as ``force``."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.orchestration.dtos import ReturnMachinesOutput
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
from orb.cli.args import build_parser
from orb.interface.request_command_handlers import handle_request_return_machines
from orb.interface.response_formatting_service import ResponseFormattingService


@pytest.mark.parametrize("flags", [[], ["--force"]])
def test_return_parser_accepts_force(flags: list[str]) -> None:
    parser, _ = build_parser()

    args = parser.parse_args(["machines", "return", *flags, "pod-a"])

    assert args.force is bool(flags)


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
async def test_force_flag_is_passed_to_the_return(force: bool) -> None:
    orchestrator = AsyncMock(spec=ReturnMachinesOrchestrator)
    orchestrator.execute.return_value = ReturnMachinesOutput(request_id=None, status="completed")
    formatter = MagicMock(spec=ResponseFormattingService)
    formatter.format_request_operation.return_value = InterfaceResponse(data={})
    container = MagicMock()
    container.get.side_effect = lambda t: {
        ReturnMachinesOrchestrator: orchestrator,
        ResponseFormattingService: formatter,
    }.get(t, MagicMock())
    args = argparse.Namespace(all=False, force=force, machine_ids=["pod-a"], machine_ids_flag=[])
    args._container = container

    await handle_request_return_machines(args)

    assert orchestrator.execute.call_args[0][0].force is force
