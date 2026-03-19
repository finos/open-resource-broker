"""Unit tests for ListRequestsOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import ListActiveRequestsQuery
from orb.application.request.queries import ListRequestsQuery
from orb.application.services.orchestration.dtos import ListRequestsInput, ListRequestsOutput
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator


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
    return ListRequestsOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestListRequestsOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_sync_false_dispatches_list_requests_query(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput(sync=False)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, ListRequestsQuery)

    @pytest.mark.asyncio
    async def test_execute_sync_true_dispatches_list_active_requests_query(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput(sync=True)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, ListActiveRequestsQuery)

    @pytest.mark.asyncio
    async def test_execute_sync_true_passes_limit_and_all_resources(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput(sync=True, limit=25)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert query.limit == 25
        assert query.all_resources is True

    @pytest.mark.asyncio
    async def test_execute_sync_false_passes_status_and_limit(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput(sync=False, status="pending", limit=10)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert query.status == "pending"
        assert query.limit == 10

    @pytest.mark.asyncio
    async def test_execute_returns_list_requests_output(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput()
        result = await orchestrator.execute(input)
        assert isinstance(result, ListRequestsOutput)
        assert hasattr(result, "requests")

    @pytest.mark.asyncio
    async def test_execute_maps_results_to_dicts(self, orchestrator, mock_query_bus):
        r = MagicMock(spec=["to_dict"])
        r.to_dict = MagicMock(return_value={"request_id": "req-1"})
        mock_query_bus.execute.return_value = [r]
        input = ListRequestsInput()
        result = await orchestrator.execute(input)
        assert result.requests == [{"request_id": "req-1"}]

    @pytest.mark.asyncio
    async def test_execute_none_result_returns_empty_list(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = None
        input = ListRequestsInput()
        result = await orchestrator.execute(input)
        assert result.requests == []

    @pytest.mark.asyncio
    async def test_execute_does_not_call_command_bus(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput()
        await orchestrator.execute(input)
        mock_command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_true_with_status_forwards_status(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput(sync=True, status="pending")
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, ListActiveRequestsQuery)
        assert query.status == "pending"

    @pytest.mark.asyncio
    async def test_sync_true_with_no_status_does_not_filter(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput(sync=True, status=None)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, ListActiveRequestsQuery)
        assert query.status is None
