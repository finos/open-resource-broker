"""Unit tests for CancelRequestOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.commands import CancelRequestCommand
from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
from orb.application.services.orchestration.dtos import CancelRequestInput, CancelRequestOutput


@pytest.fixture
def mock_command_bus():
    bus = MagicMock()
    bus.execute = AsyncMock()
    return bus


@pytest.fixture
def mock_query_bus():
    bus = MagicMock()
    bus.execute = AsyncMock()
    return bus


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def orchestrator(mock_command_bus, mock_query_bus, mock_logger):
    return CancelRequestOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestCancelRequestOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_cancel_request_command(self, orchestrator, mock_command_bus):
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
        assert result.raw["request_id"] == "req-xyz"
        assert result.raw["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_execute_uses_default_reason(self, orchestrator, mock_command_bus):
        input = CancelRequestInput(request_id="req-001")
        await orchestrator.execute(input)
        cmd = mock_command_bus.execute.call_args[0][0]
        assert cmd.reason == "Cancelled via API"

    @pytest.mark.asyncio
    async def test_execute_does_not_call_query_bus(self, orchestrator, mock_query_bus):
        input = CancelRequestInput(request_id="req-001")
        await orchestrator.execute(input)
        mock_query_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_command_bus_error_propagates(self, orchestrator, mock_command_bus):
        mock_command_bus.execute.side_effect = Exception("bus failure")
        input = CancelRequestInput(request_id="req-001")
        with pytest.raises(Exception, match="bus failure"):
            await orchestrator.execute(input)
