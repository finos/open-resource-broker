"""Unit tests for ListReturnRequestsOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import ListReturnRequestsQuery
from orb.application.services.orchestration.dtos import (
    ListReturnRequestsInput,
    ListReturnRequestsOutput,
)
from orb.application.services.orchestration.list_return_requests import (
    ListReturnRequestsOrchestrator,
)


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
    return ListReturnRequestsOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestListReturnRequestsOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_list_return_requests_query(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListReturnRequestsInput())
        mock_query_bus.execute.assert_called_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, ListReturnRequestsQuery)

    @pytest.mark.asyncio
    async def test_execute_passes_status_filter(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListReturnRequestsInput(status="pending"))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.status == "pending"

    @pytest.mark.asyncio
    async def test_execute_passes_limit(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListReturnRequestsInput(limit=20))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.limit == 20

    @pytest.mark.asyncio
    async def test_execute_returns_list_return_requests_output(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        result = await orchestrator.execute(ListReturnRequestsInput())
        assert isinstance(result, ListReturnRequestsOutput)
        assert hasattr(result, "requests")

    @pytest.mark.asyncio
    async def test_execute_maps_results_to_dicts_via_model_dump(self, orchestrator, mock_query_bus):
        r = MagicMock(spec=["model_dump"])
        r.model_dump.return_value = {"request_id": "ret-1"}
        mock_query_bus.execute.return_value = [r]
        result = await orchestrator.execute(ListReturnRequestsInput())
        assert result.requests == [{"request_id": "ret-1", "grace_period": 300}]

    @pytest.mark.asyncio
    async def test_execute_maps_results_via_to_dict_fallback(self, orchestrator, mock_query_bus):
        r = MagicMock(spec=["to_dict"])
        r.to_dict.return_value = {"request_id": "ret-2"}
        mock_query_bus.execute.return_value = [r]
        result = await orchestrator.execute(ListReturnRequestsInput())
        assert result.requests == [{"request_id": "ret-2", "grace_period": 300}]

    @pytest.mark.asyncio
    async def test_execute_grace_period_spot_override(self, orchestrator, mock_query_bus):
        r = MagicMock(spec=["model_dump"])
        r.model_dump.return_value = {"request_id": "ret-3", "price_type": "spot"}
        mock_query_bus.execute.return_value = [r]
        result = await orchestrator.execute(ListReturnRequestsInput())
        assert result.requests[0]["grace_period"] == 120

    @pytest.mark.asyncio
    async def test_execute_grace_period_not_overwritten_if_present(
        self, orchestrator, mock_query_bus
    ):
        r = MagicMock(spec=["model_dump"])
        r.model_dump.return_value = {"request_id": "ret-4", "grace_period": 60}
        mock_query_bus.execute.return_value = [r]
        result = await orchestrator.execute(ListReturnRequestsInput())
        assert result.requests[0]["grace_period"] == 60

    @pytest.mark.asyncio
    async def test_execute_none_result_returns_empty_list(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = None
        result = await orchestrator.execute(ListReturnRequestsInput())
        assert result.requests == []

    @pytest.mark.asyncio
    async def test_execute_does_not_call_command_bus(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListReturnRequestsInput())
        mock_command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_query_bus_error_propagates(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.side_effect = Exception("query failed")
        with pytest.raises(Exception, match="query failed"):
            await orchestrator.execute(ListReturnRequestsInput())
