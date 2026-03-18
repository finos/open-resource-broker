"""Orchestrator for getting provider metrics."""

from __future__ import annotations

from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.provider.queries import GetProviderMetricsQuery
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    GetProviderMetricsInput,
    GetProviderMetricsOutput,
)
from orb.domain.base.ports.logging_port import LoggingPort


class GetProviderMetricsOrchestrator(
    OrchestratorBase[GetProviderMetricsInput, GetProviderMetricsOutput]
):
    """Orchestrator for retrieving provider metrics."""

    def __init__(self, query_bus: QueryBusPort, logger: LoggingPort) -> None:
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: GetProviderMetricsInput) -> GetProviderMetricsOutput:  # type: ignore[return]
        self._logger.info(
            "GetProviderMetricsOrchestrator: provider_name=%s, timeframe=%s",
            input.provider_name,
            input.timeframe,
        )
        query = GetProviderMetricsQuery(
            provider_name=input.provider_name,
            timeframe=input.timeframe or "24h",
        )
        metrics_dto = await self._query_bus.execute(query)
        # Convert ProviderMetricsDTO to dict
        metrics_dict = metrics_dto.model_dump() if hasattr(metrics_dto, "model_dump") else {}
        return GetProviderMetricsOutput(
            metrics=metrics_dict,
            message="Provider metrics retrieved successfully",
        )
