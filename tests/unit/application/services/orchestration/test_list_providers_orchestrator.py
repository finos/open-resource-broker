"""Unit tests for ListProvidersOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.provider.queries import ListAvailableProvidersQuery
from orb.application.services.orchestration.dtos import ListProvidersInput, ListProvidersOutput
from orb.application.services.orchestration.list_providers import ListProvidersOrchestrator


@pytest.fixture
def mock_query_bus():
    bus = MagicMock()
    bus.execute = AsyncMock()
    return bus


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def orchestrator(mock_query_bus, mock_logger):
    return ListProvidersOrchestrator(
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestListProvidersOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_list_available_providers_query(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.return_value = {
            "providers": [],
            "count": 0,
            "selection_policy": "round_robin",
            "message": "Available providers retrieved successfully",
        }
        await orchestrator.execute(ListProvidersInput())
        mock_query_bus.execute.assert_called_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, ListAvailableProvidersQuery)

    @pytest.mark.asyncio
    async def test_execute_passes_provider_name_to_query(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = {
            "providers": [],
            "count": 0,
            "selection_policy": "",
            "message": "",
        }
        await orchestrator.execute(ListProvidersInput(provider_name="aws-prod"))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.provider_name == "aws-prod"

    @pytest.mark.asyncio
    async def test_execute_returns_list_providers_output(self, orchestrator, mock_query_bus):
        providers = [{"name": "aws-default", "type": "aws"}]
        mock_query_bus.execute.return_value = {
            "providers": providers,
            "count": 1,
            "selection_policy": "round_robin",
            "message": "Available providers retrieved successfully",
        }
        result = await orchestrator.execute(ListProvidersInput())
        assert isinstance(result, ListProvidersOutput)
        assert result.providers == providers
        assert result.count == 1
        assert result.selection_policy == "round_robin"

    @pytest.mark.asyncio
    async def test_execute_no_provider_name_passes_none(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = {
            "providers": [],
            "count": 0,
            "selection_policy": "",
            "message": "",
        }
        await orchestrator.execute(ListProvidersInput())
        query = mock_query_bus.execute.call_args[0][0]
        assert query.provider_name is None

    @pytest.mark.asyncio
    async def test_execute_query_error_propagates(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.side_effect = Exception("bus error")
        with pytest.raises(Exception, match="bus error"):
            await orchestrator.execute(ListProvidersInput())

    @pytest.mark.asyncio
    async def test_execute_logs_info(self, orchestrator, mock_query_bus, mock_logger):
        mock_query_bus.execute.return_value = {
            "providers": [],
            "count": 0,
            "selection_policy": "",
            "message": "",
        }
        await orchestrator.execute(ListProvidersInput())
        mock_logger.info.assert_called_once()
