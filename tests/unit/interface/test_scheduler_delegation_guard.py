"""Regression guard: every list/status handler must delegate to SchedulerPort.

These tests catch any interface handler that bypasses SchedulerPort and builds
its own response format. They will fail if a handler stops calling
scheduler.format_request_status_response.
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


def _make_namespace(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _mock_container_with_scheduler():
    """Return (container, scheduler) with all orchestrators pre-wired as AsyncMocks."""
    container = MagicMock()
    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.format_request_status_response.return_value = {"requests": []}
    scheduler.parse_request_data.return_value = []

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
        raw={"request_id": "req-123", "status": "cancelled"},
    )

    acquire_orch = AsyncMock(spec=AcquireMachinesOrchestrator)
    acquire_orch.execute.return_value = AcquireMachinesOutput(request_id="req-1", status="pending")

    dispatch_map = {
        SchedulerPort: scheduler,
        GetRequestStatusOrchestrator: status_orch,
        ListRequestsOrchestrator: list_req_orch,
        ListReturnRequestsOrchestrator: list_ret_orch,
        CancelRequestOrchestrator: cancel_orch,
        AcquireMachinesOrchestrator: acquire_orch,
    }
    container.get.side_effect = lambda t: dispatch_map.get(t, MagicMock())
    return container, scheduler


# ---------------------------------------------------------------------------
# Parametrised guard: all list/status handlers must call format_request_status_response
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
    """Every list/status handler must call scheduler.format_request_status_response."""
    module_path, fn_name = handler_fn.rsplit(".", 1)
    module = importlib.import_module(module_path)
    handler = getattr(module, fn_name)

    container, scheduler = _mock_container_with_scheduler()

    with patch(f"{module_path}.get_container", return_value=container):
        await handler(args_factory())

    scheduler.format_request_status_response.assert_called_once()


# ---------------------------------------------------------------------------
# handle_get_request_status — single ID path also delegates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_request_status_single_id_delegates_to_scheduler():
    """handle_get_request_status with a single request_id must delegate to scheduler."""
    from orb.application.services.orchestration.dtos import GetRequestStatusOutput
    from orb.interface.request_command_handlers import handle_get_request_status

    container, scheduler = _mock_container_with_scheduler()
    scheduler.format_request_status_response.return_value = {"requests": []}

    # Override the status orchestrator to return a result with the request
    status_orch = AsyncMock(spec=GetRequestStatusOrchestrator)
    status_orch.execute.return_value = GetRequestStatusOutput(
        requests=[{"request_id": "req-123", "status": "complete"}]
    )
    # Patch into the container
    original_side_effect = container.get.side_effect
    container.get.side_effect = lambda t: (
        status_orch if t is GetRequestStatusOrchestrator else original_side_effect(t)
    )

    args = _make_namespace(request_id="req-123", all=False)

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        result = await handle_get_request_status(args)

    scheduler.format_request_status_response.assert_called_once()
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# handle_request_machines — must call format_request_response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_machines_delegates_format_request_response():
    """handle_request_machines must call scheduler.format_request_response."""
    from orb.interface.request_command_handlers import handle_request_machines

    container, scheduler = _mock_container_with_scheduler()
    scheduler.parse_request_data.return_value = {
        "template_id": "t1",
        "requested_count": 1,
    }
    scheduler.format_request_response.return_value = {"requestId": "req-1", "message": "ok"}
    scheduler.get_exit_code_for_status.return_value = 0

    args = _make_namespace(template_id="t1", machine_count=1, metadata={})

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        await handle_request_machines(args)

    scheduler.format_request_response.assert_called_once()


# ---------------------------------------------------------------------------
# handle_cancel_request — must call format_request_response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_request_delegates_format_request_response():
    """handle_cancel_request must call scheduler.format_request_response."""
    from orb.interface.request_command_handlers import handle_cancel_request

    container, scheduler = _mock_container_with_scheduler()
    scheduler.format_request_response.return_value = {
        "request_id": "req-123",
        "status": "cancelled",
    }

    args = _make_namespace(request_id="req-123")

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        result = await handle_cancel_request(args)

    scheduler.format_request_response.assert_called_once()
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Scheduler mock is actually consulted (not bypassed via container.get fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_port_retrieved_from_container():
    """Container must be asked for SchedulerPort — not bypassed."""
    from orb.interface.request_command_handlers import handle_get_return_requests

    container, _ = _mock_container_with_scheduler()

    args = _make_namespace()

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        await handle_get_return_requests(args)

    # Verify SchedulerPort was requested from the container
    retrieved_types = [call.args[0] for call in container.get.call_args_list]
    assert SchedulerPort in retrieved_types, "handler must retrieve SchedulerPort from DI container"
