"""Unit tests for ListTemplatesOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import ListTemplatesQuery
from orb.application.services.orchestration.dtos import ListTemplatesInput, ListTemplatesOutput
from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator


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
    return ListTemplatesOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestListTemplatesOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_list_templates_query(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListTemplatesInput())
        mock_query_bus.execute.assert_called_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, ListTemplatesQuery)

    @pytest.mark.asyncio
    async def test_execute_passes_active_only_flag(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListTemplatesInput(active_only=False))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.active_only is False

    @pytest.mark.asyncio
    async def test_execute_passes_provider_name(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListTemplatesInput(provider_name="aws-prod"))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.provider_name == "aws-prod"

    @pytest.mark.asyncio
    async def test_execute_passes_limit(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListTemplatesInput(limit=10))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.limit == 10

    @pytest.mark.asyncio
    async def test_execute_returns_list_templates_output(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        result = await orchestrator.execute(ListTemplatesInput())
        assert isinstance(result, ListTemplatesOutput)
        assert hasattr(result, "templates")

    @pytest.mark.asyncio
    async def test_execute_returns_templates_from_query(self, orchestrator, mock_query_bus):
        t1 = MagicMock()
        t2 = MagicMock()
        mock_query_bus.execute.return_value = [t1, t2]
        result = await orchestrator.execute(ListTemplatesInput())
        assert result.templates == [t1, t2]

    @pytest.mark.asyncio
    async def test_execute_none_result_returns_empty_list(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = None
        result = await orchestrator.execute(ListTemplatesInput())
        assert result.templates == []

    @pytest.mark.asyncio
    async def test_execute_does_not_call_command_bus(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListTemplatesInput())
        mock_command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_query_bus_error_propagates(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.side_effect = Exception("query failed")
        with pytest.raises(Exception, match="query failed"):
            await orchestrator.execute(ListTemplatesInput())
