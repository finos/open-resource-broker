"""Regression guard: every list/status handler must delegate to SchedulerPort.

These tests catch any interface handler that bypasses SchedulerPort and builds
its own response format. They will fail if a handler stops calling
scheduler.format_request_status_response.
"""

import argparse
import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.ports.scheduler_port import SchedulerPort
from orb.infrastructure.di.buses import CommandBus, QueryBus


def _make_namespace(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _mock_container_with_scheduler():
    """Return (container, command_bus, query_bus, scheduler) with SchedulerPort mock."""
    container = MagicMock()
    command_bus = AsyncMock()
    query_bus = AsyncMock()
    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.format_request_status_response.return_value = {"requests": []}
    scheduler.parse_request_data.return_value = []

    dispatch_map = {CommandBus: command_bus, QueryBus: query_bus, SchedulerPort: scheduler}
    container.get.side_effect = lambda t: dispatch_map.get(t, MagicMock())
    return container, command_bus, query_bus, scheduler


# ---------------------------------------------------------------------------
# Parametrised guard: all list/status handlers must call format_request_status_response
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler_fn,args_factory,query_return",
    [
        (
            "orb.interface.request_command_handlers.handle_get_request_status",
            lambda: _make_namespace(all=True),
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

    container, command_bus, query_bus, scheduler = _mock_container_with_scheduler()
    query_bus.execute.return_value = query_return

    with patch(f"{module_path}.get_container", return_value=container):
        await handler(args_factory())

    scheduler.format_request_status_response.assert_called_once()


# ---------------------------------------------------------------------------
# handle_get_request_status — single ID path also delegates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_request_status_single_id_delegates_to_scheduler():
    """handle_get_request_status with a single request_id must delegate to scheduler."""
    from datetime import datetime, timezone

    from orb.application.request.dto import RequestDTO
    from orb.interface.request_command_handlers import handle_get_request_status

    container, command_bus, query_bus, scheduler = _mock_container_with_scheduler()
    scheduler.parse_request_data.return_value = [{"request_id": "req-123"}]
    scheduler.format_request_status_response.return_value = {"requests": []}

    dto = RequestDTO(
        request_id="req-123",
        status="complete",
        requested_count=1,
        created_at=datetime.now(timezone.utc),
    )
    query_bus.execute.return_value = dto

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

    container, command_bus, query_bus, scheduler = _mock_container_with_scheduler()
    scheduler.parse_request_data.return_value = {
        "template_id": "t1",
        "requested_count": 1,
    }
    scheduler.format_request_response.return_value = {"requestId": "req-1", "message": "ok"}
    scheduler.get_exit_code_for_status.return_value = 0

    request_dto = MagicMock()
    request_dto.status = "pending"
    request_dto.resource_ids = []
    request_dto.metadata = {}
    query_bus.execute.return_value = request_dto

    args = _make_namespace(template_id="t1", machine_count=1, metadata={})

    with (
        patch("orb.interface.request_command_handlers.get_container", return_value=container),
        patch(
            "orb.domain.request.request_identifiers.RequestId.generate",
            return_value=MagicMock(__str__=lambda self: "req-1"),
        ),
        patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active",
            return_value=False,
        ),
    ):
        await handle_request_machines(args)

    scheduler.format_request_response.assert_called_once()


# ---------------------------------------------------------------------------
# handle_cancel_request — must call format_request_response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_request_delegates_format_request_response():
    """handle_cancel_request must call scheduler.format_request_response."""
    from orb.interface.request_command_handlers import handle_cancel_request

    container, command_bus, query_bus, scheduler = _mock_container_with_scheduler()
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

    container, command_bus, query_bus, scheduler = _mock_container_with_scheduler()
    query_bus.execute.return_value = []

    args = _make_namespace()

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        await handle_get_return_requests(args)

    # Verify SchedulerPort was requested from the container
    retrieved_types = [call.args[0] for call in container.get.call_args_list]
    assert SchedulerPort in retrieved_types, "handler must retrieve SchedulerPort from DI container"
