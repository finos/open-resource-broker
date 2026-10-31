"""A successful ``requests cancel`` exits 0 even though status polling treats cancelled as a problem."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.ports.scheduler_port import SchedulerPort
from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
from orb.application.services.orchestration.dtos import CancelRequestOutput
from orb.infrastructure.scheduler.base.strategy import BaseSchedulerStrategy
from orb.interface.request_command_handlers import handle_cancel_request
from orb.interface.response_formatting_service import ResponseFormattingService


def _container(status: str):
    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.format_request_response.side_effect = lambda raw: raw
    # The real exit-code mapping used by every scheduler.
    scheduler.get_exit_code_for_status.side_effect = lambda s: (
        BaseSchedulerStrategy.get_exit_code_for_status(MagicMock(), s)
    )
    formatter = ResponseFormattingService(scheduler)
    orchestrator = AsyncMock(spec=CancelRequestOrchestrator)
    orchestrator.execute.return_value = CancelRequestOutput(request_id="req-1", status=status)
    container = MagicMock()
    container.get.side_effect = lambda key: {
        CancelRequestOrchestrator: orchestrator,
        ResponseFormattingService: formatter,
    }[key]
    return container


@pytest.mark.unit
@pytest.mark.asyncio
async def test_successful_cancel_exits_zero():
    args = argparse.Namespace(request_id="req-1", reason=None, force=False)
    args._container = _container("cancelled")

    result = await handle_cancel_request(args)

    assert isinstance(result, InterfaceResponse)
    assert result.data["status"] == "cancelled"
    assert result.exit_code == 0


@pytest.mark.unit
def test_status_polling_still_treats_cancelled_as_a_problem():
    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.format_request_response.side_effect = lambda raw: raw
    scheduler.get_exit_code_for_status.side_effect = lambda s: (
        BaseSchedulerStrategy.get_exit_code_for_status(MagicMock(), s)
    )
    response = ResponseFormattingService(scheduler).format_request_operation(
        {"request_id": "req-1", "status": "cancelled"}, "cancelled"
    )
    assert response.exit_code == 1
