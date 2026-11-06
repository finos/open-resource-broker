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


def _container(status: str, cancel_effective: bool = False, provider_abort=None):
    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.format_request_response.side_effect = lambda raw: raw
    # The real exit-code mapping used by every scheduler.
    scheduler.get_exit_code_for_status.side_effect = lambda s: (
        BaseSchedulerStrategy.get_exit_code_for_status(MagicMock(), s)
    )
    formatter = ResponseFormattingService(scheduler)
    orchestrator = AsyncMock(spec=CancelRequestOrchestrator)
    requests = [{"request_id": "req-1", "status": status}]
    if provider_abort is not None:
        requests[0]["provider_abort"] = provider_abort
    orchestrator.execute.return_value = CancelRequestOutput(
        request_id="req-1", status=status, requests=requests, cancel_effective=cancel_effective
    )
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


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["partial", "timeout"])
async def test_effective_cancel_of_a_settled_request_exits_zero_and_reports_true_status(status):
    abort = [{"resource_id": "fleet-1", "outcome": "aborted", "message": ""}]
    args = argparse.Namespace(request_id="req-1", reason=None, force=False)
    args._container = _container(status, cancel_effective=True, provider_abort=abort)

    result = await handle_cancel_request(args)

    assert result.exit_code == 0
    assert result.data["provider_abort"] == abort


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["partial", "timeout"])
async def test_cancel_that_did_nothing_keeps_the_status_mapping(status):
    args = argparse.Namespace(request_id="req-1", reason=None, force=False)
    args._container = _container(status, cancel_effective=False)

    result = await handle_cancel_request(args)

    assert result.exit_code != 0
    assert "provider_abort" not in result.data


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_abort_outcomes_are_rendered_and_nothing_else_changes():
    abort = [{"resource_id": "fleet-1", "outcome": "already_gone", "message": ""}]
    with_abort = argparse.Namespace(request_id="req-1", reason=None, force=False)
    with_abort._container = _container("cancelled", cancel_effective=True, provider_abort=abort)
    without = argparse.Namespace(request_id="req-1", reason=None, force=False)
    without._container = _container("cancelled", cancel_effective=True)

    rendered = await handle_cancel_request(with_abort)
    baseline = await handle_cancel_request(without)

    assert rendered.data["provider_abort"] == abort
    assert {k: v for k, v in rendered.data.items() if k != "provider_abort"} == baseline.data
    assert rendered.exit_code == baseline.exit_code == 0


@pytest.mark.unit
def test_rest_rendering_carries_provider_abort_for_a_settled_request():
    from orb.interface.catalog import OPERATION_CATALOG, Interface

    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.format_request_response.side_effect = lambda raw: raw
    scheduler.get_exit_code_for_status.return_value = 0
    formatter = ResponseFormattingService(scheduler)
    abort = [{"resource_id": "fleet-1", "outcome": "aborted", "message": ""}]
    out = CancelRequestOutput(
        request_id="req-1",
        status="partial",
        requests=[{"request_id": "req-1", "status": "partial", "provider_abort": abort}],
        cancel_effective=True,
    )

    entry = OPERATION_CATALOG["cancel_request"]
    data = entry.renderer_for(Interface.REST)(formatter, out).data

    assert data == {"request_id": "req-1", "status": "partial", "provider_abort": abort}


@pytest.mark.unit
def test_a_cancelled_settled_request_does_not_report_that_resources_failed():
    from orb.interface.catalog import OPERATION_CATALOG, Interface

    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.format_request_response.side_effect = lambda raw: {
        "request_id": raw["request_id"],
        "message": "Request partially completed: Some resources failed",
    }
    scheduler.get_exit_code_for_status.return_value = 0
    formatter = ResponseFormattingService(scheduler)
    abort = [{"resource_id": "fleet-1", "outcome": "aborted", "message": ""}]
    out = CancelRequestOutput(
        request_id="req-1",
        status="partial",
        requests=[{"request_id": "req-1", "status": "partial", "provider_abort": abort}],
        cancel_effective=True,
    )

    data = OPERATION_CATALOG["cancel_request"].renderer_for(Interface.REST)(formatter, out).data

    assert "failed" not in data["message"]
    assert "aborted" in data["message"] and "partial" in data["message"]
    assert data["provider_abort"] == abort
