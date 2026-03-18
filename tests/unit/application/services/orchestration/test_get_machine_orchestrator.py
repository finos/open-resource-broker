"""Unit tests for GetMachineOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import GetMachineQuery
from orb.application.services.orchestration.dtos import GetMachineInput, GetMachineOutput
from orb.application.services.orchestration.get_machine import GetMachineOrchestrator
from orb.domain.base.exceptions import EntityNotFoundError


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
    return GetMachineOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestGetMachineOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_get_machine_query(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = MagicMock()
        input = GetMachineInput(machine_id="m-001")
        await orchestrator.execute(input)
        mock_query_bus.execute.assert_called_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, GetMachineQuery)
        assert query.machine_id == "m-001"

    @pytest.mark.asyncio
    async def test_execute_returns_get_machine_output(self, orchestrator, mock_query_bus):
        machine = MagicMock()
        mock_query_bus.execute.return_value = machine
        result = await orchestrator.execute(GetMachineInput(machine_id="m-001"))
        assert isinstance(result, GetMachineOutput)
        assert result.machine is machine

    @pytest.mark.asyncio
    async def test_execute_entity_not_found_returns_none_machine(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.side_effect = EntityNotFoundError("Machine", "m-missing")
        result = await orchestrator.execute(GetMachineInput(machine_id="m-missing"))
        assert isinstance(result, GetMachineOutput)
        assert result.machine is None

    @pytest.mark.asyncio
    async def test_execute_other_exception_propagates(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.side_effect = RuntimeError("unexpected")
        with pytest.raises(RuntimeError, match="unexpected"):
            await orchestrator.execute(GetMachineInput(machine_id="m-001"))

    @pytest.mark.asyncio
    async def test_execute_does_not_call_command_bus(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        mock_query_bus.execute.return_value = MagicMock()
        await orchestrator.execute(GetMachineInput(machine_id="m-001"))
        mock_command_bus.execute.assert_not_called()
