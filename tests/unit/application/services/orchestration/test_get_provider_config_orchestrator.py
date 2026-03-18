"""Unit tests for GetProviderConfigOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.queries.system import GetProviderConfigQuery
from orb.application.services.orchestration.dtos import (
    GetProviderConfigInput,
    GetProviderConfigOutput,
)
from orb.application.services.orchestration.get_provider_config import GetProviderConfigOrchestrator


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
    return GetProviderConfigOrchestrator(
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


def _make_provider_config_dto(provider_mode="strategy", active_providers=None):
    dto = MagicMock()
    dto.model_dump.return_value = {
        "provider_mode": provider_mode,
        "active_providers": active_providers or ["aws"],
        "provider_count": len(active_providers or ["aws"]),
        "default_provider": None,
        "configuration_source": "file",
        "config_file": None,
        "template_file": None,
        "last_updated": None,
    }
    return dto


@pytest.mark.unit
@pytest.mark.application
class TestGetProviderConfigOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_get_provider_config_query(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_provider_config_dto()
        await orchestrator.execute(GetProviderConfigInput())
        mock_query_bus.execute.assert_called_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, GetProviderConfigQuery)

    @pytest.mark.asyncio
    async def test_execute_returns_get_provider_config_output(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_provider_config_dto()
        result = await orchestrator.execute(GetProviderConfigInput())
        assert isinstance(result, GetProviderConfigOutput)
        assert isinstance(result.config, dict)
        assert result.message == "Provider configuration retrieved successfully"

    @pytest.mark.asyncio
    async def test_execute_config_serialised_to_dict(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_provider_config_dto(
            provider_mode="strategy", active_providers=["aws"]
        )
        result = await orchestrator.execute(GetProviderConfigInput())
        assert result.config["provider_mode"] == "strategy"
        assert result.config["active_providers"] == ["aws"]

    @pytest.mark.asyncio
    async def test_execute_query_error_propagates(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.side_effect = Exception("bus error")
        with pytest.raises(Exception, match="bus error"):
            await orchestrator.execute(GetProviderConfigInput())

    @pytest.mark.asyncio
    async def test_execute_logs_info(self, orchestrator, mock_query_bus, mock_logger):
        mock_query_bus.execute.return_value = _make_provider_config_dto()
        await orchestrator.execute(GetProviderConfigInput())
        mock_logger.info.assert_called_once()
