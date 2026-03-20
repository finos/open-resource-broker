"""Unit tests for --wait behaviour in handle_request_machines."""

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.ports.scheduler_port import SchedulerPort
from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.dtos import AcquireMachinesOutput
from orb.interface.response_formatting_service import ResponseFormattingService


def _make_namespace(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _mock_container():
    container = MagicMock()
    scheduler = MagicMock(spec=SchedulerPort)
    acquire_orch = AsyncMock(spec=AcquireMachinesOrchestrator)
    formatter = MagicMock(spec=ResponseFormattingService)

    scheduler.parse_request_data.return_value = {
        "template_id": "t1",
        "requested_count": 1,
    }
    formatter.format_request_operation.return_value = InterfaceResponse(
        data={"requestId": "req-fixed"}, exit_code=0
    )

    container.get.side_effect = lambda t: {
        SchedulerPort: scheduler,
        AcquireMachinesOrchestrator: acquire_orch,
        ResponseFormattingService: formatter,
    }.get(t, MagicMock())

    return container, scheduler, acquire_orch, formatter


@pytest.mark.unit
class TestRequestMachinesWait:
    @pytest.mark.asyncio
    async def test_no_wait_skips_polling(self):
        """wait=False → orchestrator called with wait=False."""
        container, _, acquire_orch, _ = _mock_container()
        acquire_orch.execute.return_value = AcquireMachinesOutput(
            request_id="req-fixed", status="pending"
        )

        args = _make_namespace(template_id="t1", machine_count=1, metadata={}, wait=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            from orb.interface.request_command_handlers import handle_request_machines

            await handle_request_machines(args)

        call_input = acquire_orch.execute.call_args[0][0]
        assert call_input.wait is False

    @pytest.mark.asyncio
    async def test_timeout_zero_skips_polling(self):
        """wait=True but timeout=0 → orchestrator called with timeout_seconds=0."""
        container, _, acquire_orch, _ = _mock_container()
        acquire_orch.execute.return_value = AcquireMachinesOutput(
            request_id="req-fixed", status="pending"
        )

        args = _make_namespace(template_id="t1", machine_count=1, metadata={}, wait=True, timeout=0)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            from orb.interface.request_command_handlers import handle_request_machines

            await handle_request_machines(args)

        call_input = acquire_orch.execute.call_args[0][0]
        assert call_input.wait is True
        assert call_input.timeout_seconds == 0

    @pytest.mark.asyncio
    async def test_already_terminal_returns_immediately(self):
        """wait=True, orchestrator returns 'complete' → exit_code=0."""
        container, _, acquire_orch, formatter = _mock_container()
        acquire_orch.execute.return_value = AcquireMachinesOutput(
            request_id="req-fixed", status="complete", machine_ids=[]
        )
        formatter.format_request_operation.return_value = InterfaceResponse(
            data={"requestId": "req-fixed"}, exit_code=0
        )

        args = _make_namespace(
            template_id="t1", machine_count=1, metadata={}, wait=True, timeout=300
        )

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            from orb.interface.request_command_handlers import handle_request_machines

            result = await handle_request_machines(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_wait_passes_timeout_to_orchestrator(self):
        """wait=True, timeout=300 → orchestrator receives those values."""
        container, _, acquire_orch, _ = _mock_container()
        acquire_orch.execute.return_value = AcquireMachinesOutput(
            request_id="req-fixed", status="complete"
        )

        args = _make_namespace(
            template_id="t1", machine_count=1, metadata={}, wait=True, timeout=300
        )

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            from orb.interface.request_command_handlers import handle_request_machines

            result = await handle_request_machines(args)

        call_input = acquire_orch.execute.call_args[0][0]
        assert call_input.wait is True
        assert call_input.timeout_seconds == 300
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_failed_status_exit_code(self):
        """wait=True, orchestrator returns 'failed' → exit_code=1."""
        container, _, acquire_orch, formatter = _mock_container()
        formatter.format_request_operation.return_value = InterfaceResponse(
            data={"requestId": "req-fixed"}, exit_code=1
        )
        acquire_orch.execute.return_value = AcquireMachinesOutput(
            request_id="req-fixed", status="failed"
        )

        args = _make_namespace(
            template_id="t1", machine_count=1, metadata={}, wait=True, timeout=300
        )

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            from orb.interface.request_command_handlers import handle_request_machines

            result = await handle_request_machines(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1
