"""Unit tests for --wait behaviour in handle_request_machines."""

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.ports.scheduler_port import SchedulerPort
from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.dtos import AcquireMachinesOutput


def _make_namespace(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _mock_container():
    container = MagicMock()
    scheduler = MagicMock(spec=SchedulerPort)
    acquire_orch = AsyncMock(spec=AcquireMachinesOrchestrator)

    scheduler.parse_request_data.return_value = {
        "template_id": "t1",
        "requested_count": 1,
    }
    scheduler.format_request_response.return_value = {"requestId": "req-fixed"}
    scheduler.get_exit_code_for_status.return_value = 0

    container.get.side_effect = lambda t: {
        SchedulerPort: scheduler,
        AcquireMachinesOrchestrator: acquire_orch,
    }.get(t, MagicMock())

    return container, scheduler, acquire_orch


@pytest.mark.unit
class TestRequestMachinesWait:
    @pytest.mark.asyncio
    async def test_no_wait_skips_polling(self):
        """wait=False → orchestrator called with wait=False."""
        container, scheduler, acquire_orch = _mock_container()
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
        container, scheduler, acquire_orch = _mock_container()
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
        container, scheduler, acquire_orch = _mock_container()
        acquire_orch.execute.return_value = AcquireMachinesOutput(
            request_id="req-fixed", status="complete", machine_ids=[]
        )

        args = _make_namespace(
            template_id="t1", machine_count=1, metadata={}, wait=True, timeout=300
        )

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            from orb.interface.request_command_handlers import handle_request_machines

            result = await handle_request_machines(args)

        assert isinstance(result, tuple)
        _, exit_code = result
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_wait_passes_timeout_to_orchestrator(self):
        """wait=True, timeout=300 → orchestrator receives those values."""
        container, scheduler, acquire_orch = _mock_container()
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
        assert isinstance(result, tuple)

    @pytest.mark.asyncio
    async def test_failed_status_exit_code(self):
        """wait=True, orchestrator returns 'failed' → exit_code=1."""
        container, scheduler, acquire_orch = _mock_container()
        scheduler.get_exit_code_for_status.return_value = 1
        acquire_orch.execute.return_value = AcquireMachinesOutput(
            request_id="req-fixed", status="failed"
        )

        args = _make_namespace(
            template_id="t1", machine_count=1, metadata={}, wait=True, timeout=300
        )

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            from orb.interface.request_command_handlers import handle_request_machines

            result = await handle_request_machines(args)

        assert isinstance(result, tuple)
        _, exit_code = result
        assert exit_code == 1
