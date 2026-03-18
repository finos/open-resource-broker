"""Unit tests for GetProviderMetricsOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.provider.queries import GetProviderMetricsQuery
from orb.application.services.orchestration.dtos import (
    GetProviderMetricsInput,
    GetProviderMetricsOutput,
)
from orb.application.services.orchestration.get_provider_metrics import (
    GetProviderMetricsOrchestrator,
)


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
    return GetProviderMetricsOrchestrator(
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


def _make_metrics_dto(provider_name="aws", total_requests=10):
    dto = MagicMock()
    dto.model_dump.return_value = {
        "provider_name": provider_name,
        "total_requests": total_requests,
        "successful_requests": total_requests,
        "failed_requests": 0,
        "average_response_time_ms": 0.0,
        "error_rate_percent": 0.0,
        "throughput_per_minute": 0.0,
        "last_request_time": None,
        "uptime_percent": 100.0,
        "health_status": "healthy",
    }
    return dto


@pytest.mark.unit
@pytest.mark.application
class TestGetProviderMetricsOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_get_provider_metrics_query(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.return_value = _make_metrics_dto()
        await orchestrator.execute(GetProviderMetricsInput())
        mock_query_bus.execute.assert_called_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, GetProviderMetricsQuery)

    @pytest.mark.asyncio
    async def test_execute_passes_provider_name_to_query(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_metrics_dto()
        await orchestrator.execute(GetProviderMetricsInput(provider_name="aws-prod"))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.provider_name == "aws-prod"

    @pytest.mark.asyncio
    async def test_execute_passes_timeframe_to_query(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_metrics_dto()
        await orchestrator.execute(GetProviderMetricsInput(timeframe="7d"))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.timeframe == "7d"

    @pytest.mark.asyncio
    async def test_execute_default_timeframe_is_24h(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_metrics_dto()
        await orchestrator.execute(GetProviderMetricsInput())
        query = mock_query_bus.execute.call_args[0][0]
        assert query.timeframe == "24h"

    @pytest.mark.asyncio
    async def test_execute_returns_get_provider_metrics_output(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_metrics_dto()
        result = await orchestrator.execute(GetProviderMetricsInput())
        assert isinstance(result, GetProviderMetricsOutput)
        assert isinstance(result.metrics, dict)
        assert result.message == "Provider metrics retrieved successfully"

    @pytest.mark.asyncio
    async def test_execute_metrics_serialised_to_dict(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_metrics_dto(
            provider_name="aws", total_requests=10
        )
        result = await orchestrator.execute(GetProviderMetricsInput())
        assert result.metrics["provider_name"] == "aws"
        assert result.metrics["total_requests"] == 10

    @pytest.mark.asyncio
    async def test_execute_none_provider_name_passes_none(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_metrics_dto()
        await orchestrator.execute(GetProviderMetricsInput())
        query = mock_query_bus.execute.call_args[0][0]
        assert query.provider_name is None

    @pytest.mark.asyncio
    async def test_execute_query_error_propagates(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.side_effect = Exception("bus error")
        with pytest.raises(Exception, match="bus error"):
            await orchestrator.execute(GetProviderMetricsInput())

    @pytest.mark.asyncio
    async def test_execute_logs_info(self, orchestrator, mock_query_bus, mock_logger):
        mock_query_bus.execute.return_value = _make_metrics_dto()
        await orchestrator.execute(GetProviderMetricsInput(provider_name="aws"))
        mock_logger.info.assert_called_once()
