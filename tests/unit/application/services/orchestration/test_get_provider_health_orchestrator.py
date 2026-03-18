"""Unit tests for GetProviderHealthOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.provider.queries import GetProviderHealthQuery
from orb.application.services.orchestration.dtos import (
    GetProviderHealthInput,
    GetProviderHealthOutput,
)
from orb.application.services.orchestration.get_provider_health import GetProviderHealthOrchestrator


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
    return GetProviderHealthOrchestrator(
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestGetProviderHealthOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_get_provider_health_query(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = {"status": "operational", "components": {}}
        input = GetProviderHealthInput()
        await orchestrator.execute(input)
        mock_query_bus.execute.assert_called_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, GetProviderHealthQuery)

    @pytest.mark.asyncio
    async def test_execute_returns_health_output(self, orchestrator, mock_query_bus):
        health_data = {"status": "healthy", "provider": "aws"}
        mock_query_bus.execute.return_value = health_data
        result = await orchestrator.execute(GetProviderHealthInput())
        assert isinstance(result, GetProviderHealthOutput)
        assert result.health == health_data
        assert result.message == "Provider health retrieved successfully"

    @pytest.mark.asyncio
    async def test_execute_query_error_propagates(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.side_effect = Exception("bus error")
        with pytest.raises(Exception, match="bus error"):
            await orchestrator.execute(GetProviderHealthInput())

    @pytest.mark.asyncio
    async def test_execute_logs_info(self, orchestrator, mock_query_bus, mock_logger):
        mock_query_bus.execute.return_value = {}
        await orchestrator.execute(GetProviderHealthInput(provider_name="aws"))
        mock_logger.info.assert_called_once()
        assert "aws" in str(mock_logger.info.call_args)

    @pytest.mark.asyncio
    async def test_execute_passes_provider_name_to_query(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = {}
        await orchestrator.execute(GetProviderHealthInput(provider_name="aws-prod"))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.provider_name == "aws-prod"

    @pytest.mark.asyncio
    async def test_execute_none_provider_name_passes_none(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = {}
        await orchestrator.execute(GetProviderHealthInput())
        query = mock_query_bus.execute.call_args[0][0]
        assert query.provider_name is None
