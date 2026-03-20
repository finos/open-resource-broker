"""Regression guard: every list/status handler must delegate to ResponseFormattingService.

These tests catch any interface handler that bypasses ResponseFormattingService and builds
its own response format. They will fail if a handler stops calling
formatter.format_request_status or formatter.format_request_operation.
"""

import argparse
import importlib
from functools import partial
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.ports.scheduler_port import SchedulerPort
from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
from orb.application.services.orchestration.dtos import (
    AcquireMachinesOutput,
    CancelRequestOutput,
    GetRequestStatusOutput,
    ListRequestsOutput,
    ListReturnRequestsOutput,
)
from orb.application.services.orchestration.get_request_status import GetRequestStatusOrchestrator
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
from orb.application.services.orchestration.list_return_requests import (
    ListReturnRequestsOrchestrator,
)
from orb.interface.response_formatting_service import ResponseFormattingService


def _make_namespace(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _mock_container_with_formatter():
    """Return (container, formatter) with all orchestrators pre-wired as AsyncMocks."""
    from orb.application.dto.interface_response import InterfaceResponse

    container = MagicMock()
    scheduler = MagicMock(spec=SchedulerPort)
    formatter = MagicMock(spec=ResponseFormattingService)
    formatter.format_request_status.return_value = InterfaceResponse(data={"requests": []})
    formatter.format_request_operation.return_value = InterfaceResponse(
        data={"request_id": "req-1"}
    )

    status_orch = AsyncMock(spec=GetRequestStatusOrchestrator)
    status_orch.execute.return_value = GetRequestStatusOutput(requests=[])

    list_req_orch = AsyncMock(spec=ListRequestsOrchestrator)
    list_req_orch.execute.return_value = ListRequestsOutput(requests=[])

    list_ret_orch = AsyncMock(spec=ListReturnRequestsOrchestrator)
    list_ret_orch.execute.return_value = ListReturnRequestsOutput(requests=[])

    cancel_orch = AsyncMock(spec=CancelRequestOrchestrator)
    cancel_orch.execute.return_value = CancelRequestOutput(
        request_id="req-123",
        status="cancelled",
    )

    acquire_orch = AsyncMock(spec=AcquireMachinesOrchestrator)
    acquire_orch.execute.return_value = AcquireMachinesOutput(request_id="req-1", status="pending")

    dispatch_map = {
        SchedulerPort: scheduler,
        ResponseFormattingService: formatter,
        GetRequestStatusOrchestrator: status_orch,
        ListRequestsOrchestrator: list_req_orch,
        ListReturnRequestsOrchestrator: list_ret_orch,
        CancelRequestOrchestrator: cancel_orch,
        AcquireMachinesOrchestrator: acquire_orch,
    }
    container.get.side_effect = lambda t: dispatch_map.get(t, MagicMock())
    return container, formatter


# ---------------------------------------------------------------------------
# Parametrised guard: all list/status handlers must call format_request_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler_fn,args_factory,query_return",
    [
        (
            "orb.interface.request_command_handlers.handle_get_request_status",
            partial(_make_namespace, all=True),
            [],
        ),
        (
            "orb.interface.request_command_handlers.handle_list_requests",
            _make_namespace,
            [],
        ),
        (
            "orb.interface.request_command_handlers.handle_get_return_requests",
            _make_namespace,
            [],
        ),
    ],
)
@pytest.mark.asyncio
async def test_handler_delegates_to_scheduler(handler_fn, args_factory, query_return):
    """Every list/status handler must call formatter.format_request_status."""
    module_path, fn_name = handler_fn.rsplit(".", 1)
    module = importlib.import_module(module_path)
    handler = getattr(module, fn_name)

    container, formatter = _mock_container_with_formatter()

    with patch(f"{module_path}.get_container", return_value=container):
        await handler(args_factory())

    formatter.format_request_status.assert_called_once()


# ---------------------------------------------------------------------------
# handle_get_request_status — single ID path also delegates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_request_status_single_id_delegates_to_scheduler():
    """handle_get_request_status with a single request_id must delegate to formatter."""
    from orb.application.dto.interface_response import InterfaceResponse
    from orb.interface.request_command_handlers import handle_get_request_status

    container, formatter = _mock_container_with_formatter()
    formatter.format_request_status.return_value = InterfaceResponse(data={"requests": []})

    # Override the status orchestrator to return a result with the request
    status_orch = AsyncMock(spec=GetRequestStatusOrchestrator)
    status_orch.execute.return_value = GetRequestStatusOutput(
        requests=[{"request_id": "req-123", "status": "complete"}]
    )
    original_side_effect = container.get.side_effect
    container.get.side_effect = lambda t: (
        status_orch if t is GetRequestStatusOrchestrator else original_side_effect(t)
    )

    args = _make_namespace(request_id="req-123", all=False)

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        result = await handle_get_request_status(args)

    formatter.format_request_status.assert_called_once()
    from orb.application.dto.interface_response import InterfaceResponse as IR

    assert isinstance(result, IR)


# ---------------------------------------------------------------------------
# handle_request_machines — must call format_request_operation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_machines_delegates_format_request_response():
    """handle_request_machines must call formatter.format_request_operation."""
    from orb.application.dto.interface_response import InterfaceResponse
    from orb.interface.request_command_handlers import handle_request_machines

    container, formatter = _mock_container_with_formatter()
    formatter.format_request_operation.return_value = InterfaceResponse(
        data={"requestId": "req-1", "message": "ok"}, exit_code=0
    )

    # scheduler.parse_request_data is still needed for input_data path — wire it
    scheduler = container.get(SchedulerPort)
    scheduler.parse_request_data.return_value = {
        "template_id": "t1",
        "requested_count": 1,
    }

    args = _make_namespace(template_id="t1", machine_count=1, metadata={})

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        await handle_request_machines(args)

    formatter.format_request_operation.assert_called_once()


# ---------------------------------------------------------------------------
# handle_cancel_request — must call format_request_operation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_request_delegates_format_request_response():
    """handle_cancel_request must call formatter.format_request_operation."""
    from orb.application.dto.interface_response import InterfaceResponse
    from orb.interface.request_command_handlers import handle_cancel_request

    container, formatter = _mock_container_with_formatter()
    formatter.format_request_operation.return_value = InterfaceResponse(
        data={"request_id": "req-123", "status": "cancelled"}
    )

    args = _make_namespace(request_id="req-123")

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        result = await handle_cancel_request(args)

    formatter.format_request_operation.assert_called_once()
    from orb.application.dto.interface_response import InterfaceResponse as IR

    assert isinstance(result, IR)


# ---------------------------------------------------------------------------
# ResponseFormattingService is actually consulted (not bypassed via container.get fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_port_retrieved_from_container():
    """Container must be asked for ResponseFormattingService — not bypassed."""
    from orb.interface.request_command_handlers import handle_get_return_requests

    container, _ = _mock_container_with_formatter()

    args = _make_namespace()

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        await handle_get_return_requests(args)

    # Verify ResponseFormattingService was requested from the container
    retrieved_types = [call.args[0] for call in container.get.call_args_list]
    assert ResponseFormattingService in retrieved_types, (
        "handler must retrieve ResponseFormattingService from DI container"
    )
