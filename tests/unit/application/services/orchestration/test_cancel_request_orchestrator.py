"""Unit tests for CancelRequestOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.commands import CancelRequestCommand
from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
from orb.application.services.orchestration.dtos import (
    CancelRequestInput,
    CancelRequestOutput,
    ReturnMachinesOutput,
)


@pytest.fixture
def mock_command_bus():
    bus = MagicMock()
    bus.execute = AsyncMock()
    return bus


@pytest.fixture
def mock_query_bus():
    bus = MagicMock()
    # Default: query bus returns a request with no machine_ids so the
    # orchestrator skips the return-then-cancel path. Individual tests
    # override this for the machines-allocated cases.
    bus.execute = AsyncMock(return_value=MagicMock(machine_ids=[]))
    return bus


@pytest.fixture
def mock_return_orchestrator():
    orch = MagicMock()
    orch.execute = AsyncMock(
        return_value=ReturnMachinesOutput(
            request_id="ret-001",
            status="complete",
            message="Returned",
        )
    )
    return orch


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def orchestrator(mock_command_bus, mock_query_bus, mock_return_orchestrator, mock_logger):
    return CancelRequestOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        return_orchestrator=mock_return_orchestrator,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestCancelRequestOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_cancel_request_command(
        self, orchestrator, mock_command_bus
    ):
        input = CancelRequestInput(request_id="req-001", reason="no longer needed")
        await orchestrator.execute(input)
        mock_command_bus.execute.assert_called_once()
        cmd = mock_command_bus.execute.call_args[0][0]
        assert isinstance(cmd, CancelRequestCommand)
        assert cmd.request_id == "req-001"
        assert cmd.reason == "no longer needed"

    @pytest.mark.asyncio
    async def test_execute_returns_cancelled_status(self, orchestrator):
        input = CancelRequestInput(request_id="req-001")
        result = await orchestrator.execute(input)
        assert isinstance(result, CancelRequestOutput)
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_execute_returns_correct_request_id(self, orchestrator):
        input = CancelRequestInput(request_id="req-abc")
        result = await orchestrator.execute(input)
        assert result.request_id == "req-abc"

    @pytest.mark.asyncio
    async def test_execute_raw_contains_request_id_and_status(self, orchestrator):
        input = CancelRequestInput(request_id="req-xyz")
        result = await orchestrator.execute(input)
        assert result.request_id == "req-xyz"
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_execute_uses_default_reason(self, orchestrator, mock_command_bus):
        input = CancelRequestInput(request_id="req-001")
        await orchestrator.execute(input)
        cmd = mock_command_bus.execute.call_args[0][0]
        assert cmd.reason == "Cancelled via API"

    @pytest.mark.asyncio
    async def test_execute_calls_query_bus_to_collect_machine_ids(
        self, orchestrator, mock_query_bus
    ):
        """Cancel now needs the request's machine_ids, so it consults the query bus."""
        input = CancelRequestInput(request_id="req-001")
        await orchestrator.execute(input)
        mock_query_bus.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_skips_return_when_no_machines(
        self, orchestrator, mock_return_orchestrator
    ):
        """No machines allocated → return orchestrator is never invoked."""
        input = CancelRequestInput(request_id="req-001")
        await orchestrator.execute(input)
        mock_return_orchestrator.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_dispatches_return_when_machines_exist(
        self, mock_command_bus, mock_logger, mock_return_orchestrator
    ):
        """Machines allocated → return orchestrator is invoked with their IDs first."""
        query_bus = MagicMock()
        query_bus.execute = AsyncMock(
            return_value=MagicMock(machine_ids=["i-aaa", "i-bbb"])
        )
        orchestrator = CancelRequestOrchestrator(
            command_bus=mock_command_bus,
            query_bus=query_bus,
            return_orchestrator=mock_return_orchestrator,
            logger=mock_logger,
        )
        result = await orchestrator.execute(CancelRequestInput(request_id="req-001"))

        mock_return_orchestrator.execute.assert_called_once()
        return_input = mock_return_orchestrator.execute.call_args[0][0]
        assert sorted(return_input.machine_ids) == ["i-aaa", "i-bbb"]
        # Cancel still flips status after the return.
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_execute_return_failure_still_dispatches_cancel(
        self, mock_command_bus, mock_logger, mock_return_orchestrator
    ):
        """If Return fails, we still mark the request cancelled and surface the error."""
        query_bus = MagicMock()
        query_bus.execute = AsyncMock(return_value=MagicMock(machine_ids=["i-aaa"]))
        mock_return_orchestrator.execute = AsyncMock(side_effect=Exception("aws boom"))
        orchestrator = CancelRequestOrchestrator(
            command_bus=mock_command_bus,
            query_bus=query_bus,
            return_orchestrator=mock_return_orchestrator,
            logger=mock_logger,
        )
        result = await orchestrator.execute(CancelRequestInput(request_id="req-001"))
        mock_command_bus.execute.assert_called_once()
        # The first (only) request entry carries the return failure detail.
        entry = result.requests[0]
        assert entry["return_status"] == "failed"
        assert "aws boom" in entry["return_message"]

    @pytest.mark.asyncio
    async def test_execute_command_bus_error_propagates(self, orchestrator, mock_command_bus):
        mock_command_bus.execute.side_effect = Exception("bus failure")
        input = CancelRequestInput(request_id="req-001")
        with pytest.raises(Exception, match="bus failure"):
            await orchestrator.execute(input)
