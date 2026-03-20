"""Cross-interface response consistency tests.

Verifies that CLI handlers delegate to ResponseFormattingService for formatting, and that
the same scheduler produces identical output regardless of which interface calls it.
These tests document the expected post-fix behaviour for REST/MCP (ticket 1910).
"""

import argparse
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.ports.scheduler_port import SchedulerPort
from orb.application.request.dto import RequestDTO
from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.dtos import (
    AcquireMachinesOutput,
    GetRequestStatusOutput,
)
from orb.application.services.orchestration.get_request_status import GetRequestStatusOrchestrator
from orb.interface.response_formatting_service import ResponseFormattingService


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


def _mock_container(formatter_status_return: dict, status_orch_requests: list | None = None):
    """Build a mock DI container with orchestrators, scheduler, and formatter."""
    from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
    from orb.application.services.orchestration.dtos import (
        CancelRequestOutput,
        ListRequestsOutput,
        ListReturnRequestsOutput,
    )
    from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
    from orb.application.services.orchestration.list_return_requests import (
        ListReturnRequestsOrchestrator,
    )

    container = MagicMock()
    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.parse_request_data.return_value = [{"request_id": "req-abc"}]

    formatter = MagicMock(spec=ResponseFormattingService)
    formatter.format_request_status.return_value = InterfaceResponse(data=formatter_status_return)
    formatter.format_request_operation.return_value = InterfaceResponse(
        data={"requestId": "req-1", "message": "ok"}, exit_code=0
    )

    status_orch = AsyncMock(spec=GetRequestStatusOrchestrator)
    status_orch.execute.return_value = GetRequestStatusOutput(
        requests=status_orch_requests
        if status_orch_requests is not None
        else [{"request_id": "req-abc", "status": "complete"}]
    )

    list_req_orch = AsyncMock(spec=ListRequestsOrchestrator)
    list_req_orch.execute.return_value = ListRequestsOutput(requests=[])

    list_ret_orch = AsyncMock(spec=ListReturnRequestsOrchestrator)
    list_ret_orch.execute.return_value = ListReturnRequestsOutput(requests=[])

    cancel_orch = AsyncMock(spec=CancelRequestOrchestrator)
    cancel_orch.execute.return_value = CancelRequestOutput(request_id="req-abc", status="cancelled")

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
    return container, formatter, status_orch, acquire_orch


# ---------------------------------------------------------------------------
# 4b. CLI calls formatter.format_request_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_calls_format_request_status_response():
    """CLI handle_get_request_status must delegate to formatter.format_request_status."""
    expected_data = {"requests": [{"requestId": "req-abc", "status": "complete"}]}
    container, formatter, *_ = _mock_container(expected_data)

    args = _make_namespace(request_id="req-abc", all=False)

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        from orb.interface.request_command_handlers import handle_get_request_status

        result = await handle_get_request_status(args)

    formatter.format_request_status.assert_called_once()
    assert isinstance(result, InterfaceResponse)
    assert result.data == expected_data


@pytest.mark.asyncio
async def test_cli_format_receives_list():
    """formatter.format_request_status must be called with a list."""
    container, formatter, *_ = _mock_container({"requests": []})

    args = _make_namespace(request_id="req-abc", all=False)

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        from orb.interface.request_command_handlers import handle_get_request_status

        await handle_get_request_status(args)

    call_args = formatter.format_request_status.call_args[0][0]
    assert isinstance(call_args, list)


@pytest.mark.asyncio
async def test_cli_all_flag_delegates_to_scheduler():
    """CLI handle_get_request_status with all=True must still delegate to formatter."""
    expected_data = {"requests": []}
    container, formatter, _, _ = _mock_container(expected_data, status_orch_requests=[])

    args = _make_namespace(all=True)

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        from orb.interface.request_command_handlers import handle_get_request_status

        result = await handle_get_request_status(args)

    formatter.format_request_status.assert_called_once()
    assert isinstance(result, InterfaceResponse)
    assert result.data == expected_data


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
    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
    from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )

    dto = _make_request_dto("complete")

    hf_result = HostFactorySchedulerStrategy().format_request_status_response([dto])
    default_result = DefaultSchedulerStrategy().format_request_status_response([dto])

    hf_req = hf_result["requests"][0]
    default_req = default_result["requests"][0]

    assert "requestId" in hf_req, "HF must use camelCase requestId"
    assert "requestId" not in default_req, "default must not use camelCase requestId"


# ---------------------------------------------------------------------------
# 4e. handle_request_machines delegates format_request_operation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_request_machines_delegates_format_request_response():
    """handle_request_machines must call formatter.format_request_operation, not build response itself."""
    container, formatter, _, acquire_orch = _mock_container({})
    scheduler = container.get(SchedulerPort)
    scheduler.parse_request_data.return_value = {"template_id": "t1", "requested_count": 1}
    acquire_orch.execute.return_value = AcquireMachinesOutput(
        request_id="req-1",
        status="pending",
        machine_ids=[],
    )

    args = _make_namespace(template_id="t1", machine_count=1, metadata={})

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        from orb.interface.request_command_handlers import handle_request_machines

        await handle_request_machines(args)

    formatter.format_request_operation.assert_called_once()
    call_arg = formatter.format_request_operation.call_args[0][0]
    assert "request_id" in call_arg


# ---------------------------------------------------------------------------
# 4f. Return value from formatter is passed through unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_passes_scheduler_return_value_through():
    """The return value from formatter.format_request_status must be the handler result."""
    sentinel_data = {"requests": [{"requestId": "sentinel-value", "status": "complete"}]}
    container, formatter, _, _ = _mock_container(sentinel_data, status_orch_requests=[])

    args = _make_namespace(all=True)

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        from orb.interface.request_command_handlers import handle_get_request_status

        result = await handle_get_request_status(args)

    assert isinstance(result, InterfaceResponse)
    assert result.data == sentinel_data
