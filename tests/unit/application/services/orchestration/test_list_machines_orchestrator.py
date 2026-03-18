"""Unit tests for ListMachinesOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import ListMachinesQuery
from orb.application.services.orchestration.dtos import ListMachinesInput, ListMachinesOutput
from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator


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
    return ListMachinesOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestListMachinesOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_list_machines_query(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        input = ListMachinesInput()
        await orchestrator.execute(input)
        mock_query_bus.execute.assert_called_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, ListMachinesQuery)

    @pytest.mark.asyncio
    async def test_execute_passes_all_filter_fields(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        input = ListMachinesInput(
            status="active", provider_name="aws-prod", request_id="req-1", limit=25
        )
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert query.status == "active"
        assert query.provider_name == "aws-prod"
        assert query.request_id == "req-1"
        assert query.limit == 25

    @pytest.mark.asyncio
    async def test_execute_returns_list_machines_output(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        result = await orchestrator.execute(ListMachinesInput())
        assert isinstance(result, ListMachinesOutput)
        assert hasattr(result, "machines")

    @pytest.mark.asyncio
    async def test_execute_returns_machines_from_query(self, orchestrator, mock_query_bus):
        m1 = MagicMock()
        m2 = MagicMock()
        mock_query_bus.execute.return_value = [m1, m2]
        result = await orchestrator.execute(ListMachinesInput())
        assert result.machines == [m1, m2]

    @pytest.mark.asyncio
    async def test_execute_none_result_returns_empty_list(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = None
        result = await orchestrator.execute(ListMachinesInput())
        assert result.machines == []

    @pytest.mark.asyncio
    async def test_execute_does_not_call_command_bus(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListMachinesInput())
        mock_command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_query_bus_error_propagates(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.side_effect = Exception("query failed")
        with pytest.raises(Exception, match="query failed"):
            await orchestrator.execute(ListMachinesInput())
