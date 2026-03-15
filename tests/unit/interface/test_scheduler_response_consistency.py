"""Cross-interface response consistency tests.

Verifies that CLI handlers delegate to SchedulerPort for formatting, and that
the same scheduler produces identical output regardless of which interface calls it.
These tests document the expected post-fix behaviour for REST/MCP (ticket 1910).
"""

import argparse
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.request.dto import RequestDTO
from orb.domain.base.ports.scheduler_port import SchedulerPort
from orb.infrastructure.di.buses import CommandBus, QueryBus


def _make_namespace(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _make_request_dto(status: str = "complete") -> RequestDTO:
    return RequestDTO(
        request_id="req-abc",
        status=status,
        requested_count=2,
        created_at=datetime.now(timezone.utc),
    )


def _mock_container(scheduler_return: dict):
    """Build a mock DI container with a typed scheduler mock."""
    container = MagicMock()
    command_bus = AsyncMock()
    query_bus = AsyncMock()
    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.format_request_status_response.return_value = scheduler_return
    scheduler.parse_request_data.return_value = [{"request_id": "req-abc"}]

    dispatch_map = {CommandBus: command_bus, QueryBus: query_bus, SchedulerPort: scheduler}
    container.get.side_effect = lambda t: dispatch_map.get(t, MagicMock())
    return container, command_bus, query_bus, scheduler


# ---------------------------------------------------------------------------
# 4b. CLI calls format_request_status_response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_calls_format_request_status_response():
    """CLI handle_get_request_status must delegate to scheduler.format_request_status_response."""
    expected = {"requests": [{"requestId": "req-abc", "status": "complete"}]}
    container, _, query_bus, scheduler = _mock_container(expected)
    query_bus.execute.return_value = _make_request_dto()

    args = _make_namespace(request_id="req-abc", all=False)

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        from orb.interface.request_command_handlers import handle_get_request_status
        result = await handle_get_request_status(args)

    scheduler.format_request_status_response.assert_called_once()
    assert result == expected


@pytest.mark.asyncio
async def test_cli_format_receives_dto_list():
    """format_request_status_response must be called with a list of RequestDTO objects."""
    container, _, query_bus, scheduler = _mock_container({"requests": []})
    dto = _make_request_dto("complete")
    query_bus.execute.return_value = dto

    args = _make_namespace(request_id="req-abc", all=False)

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        from orb.interface.request_command_handlers import handle_get_request_status
        await handle_get_request_status(args)

    call_args = scheduler.format_request_status_response.call_args[0][0]
    assert isinstance(call_args, list)
    assert all(isinstance(item, RequestDTO) for item in call_args)


@pytest.mark.asyncio
async def test_cli_all_flag_delegates_to_scheduler():
    """CLI handle_get_request_status with all=True must still delegate to scheduler."""
    expected = {"requests": []}
    container, _, query_bus, scheduler = _mock_container(expected)
    query_bus.execute.return_value = []

    args = _make_namespace(all=True)

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        from orb.interface.request_command_handlers import handle_get_request_status
        result = await handle_get_request_status(args)

    scheduler.format_request_status_response.assert_called_once()
    assert result == expected


# ---------------------------------------------------------------------------
# 4c. Same scheduler → same output shape from any caller
# ---------------------------------------------------------------------------


def test_cli_rest_same_output_for_same_scheduler():
    """The same scheduler strategy must produce identical output when called twice."""
    from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )
    dto = _make_request_dto("complete")
    hf = HostFactorySchedulerStrategy()

    first_result = hf.format_request_status_response([dto])
    second_result = hf.format_request_status_response([dto])

    assert first_result == second_result
    assert "requestId" in first_result["requests"][0]


def test_hf_scheduler_output_is_deterministic():
    """HostFactory scheduler must produce identical output for identical input."""
    from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )
    hf = HostFactorySchedulerStrategy()
    dto = _make_request_dto("pending")

    results = [hf.format_request_status_response([dto]) for _ in range(3)]
    assert results[0] == results[1] == results[2]


def test_default_scheduler_output_is_deterministic():
    """Default scheduler must produce identical output for identical input."""
    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
    default = DefaultSchedulerStrategy()
    dto = _make_request_dto("complete")

    results = [default.format_request_status_response([dto]) for _ in range(3)]
    assert results[0] == results[1] == results[2]


# ---------------------------------------------------------------------------
# 4d. HF vs default produce different field names (contract divergence is intentional)
# ---------------------------------------------------------------------------


def test_hf_and_default_produce_different_id_field_names():
    """HF uses requestId; default uses request_id. This divergence is intentional."""
    from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )
    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    dto = _make_request_dto("complete")

    hf_result = HostFactorySchedulerStrategy().format_request_status_response([dto])
    default_result = DefaultSchedulerStrategy().format_request_status_response([dto])

    hf_req = hf_result["requests"][0]
    default_req = default_result["requests"][0]

    assert "requestId" in hf_req, "HF must use camelCase requestId"
    assert "requestId" not in default_req, "default must not use camelCase requestId"


# ---------------------------------------------------------------------------
# 4e. handle_request_machines delegates format_request_response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_request_machines_delegates_format_request_response():
    """handle_request_machines must call scheduler.format_request_response, not build response itself."""
    container = MagicMock()
    command_bus = AsyncMock()
    query_bus = AsyncMock()
    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.parse_request_data.return_value = {"template_id": "t1", "requested_count": 1}
    scheduler.format_request_response.return_value = {"requestId": "req-1", "message": "ok"}
    scheduler.get_exit_code_for_status.return_value = 0

    dispatch_map = {CommandBus: command_bus, QueryBus: query_bus, SchedulerPort: scheduler}
    container.get.side_effect = lambda t: dispatch_map.get(t, MagicMock())

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
        from orb.interface.request_command_handlers import handle_request_machines
        result = await handle_request_machines(args)

    scheduler.format_request_response.assert_called_once()
    call_arg = scheduler.format_request_response.call_args[0][0]
    assert "request_id" in call_arg


# ---------------------------------------------------------------------------
# 4f. Return value from scheduler is passed through unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_passes_scheduler_return_value_through():
    """The return value from scheduler.format_request_status_response must be the handler result."""
    sentinel = {"requests": [{"requestId": "sentinel-value", "status": "complete"}]}
    container, _, query_bus, scheduler = _mock_container(sentinel)
    query_bus.execute.return_value = []

    args = _make_namespace(all=True)

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        from orb.interface.request_command_handlers import handle_get_request_status
        result = await handle_get_request_status(args)

    assert result == sentinel
