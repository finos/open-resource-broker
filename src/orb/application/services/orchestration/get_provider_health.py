"""Orchestrator for getting provider health status."""

from __future__ import annotations

from typing import cast

from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.provider.queries import GetProviderHealthQuery
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    GetProviderHealthInput,
    GetProviderHealthOutput,
)
from orb.domain.base.ports.logging_port import LoggingPort


class GetProviderHealthOrchestrator(
    OrchestratorBase[GetProviderHealthInput, GetProviderHealthOutput]
):
    """Orchestrator for retrieving provider health status."""

    def __init__(self, query_bus: QueryBusPort, logger: LoggingPort) -> None:
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: GetProviderHealthInput) -> GetProviderHealthOutput:  # type: ignore[return]
        self._logger.info("GetProviderHealthOrchestrator: provider_name=%s", input.provider_name)
        query = GetProviderHealthQuery(provider_name=input.provider_name)
        health = await self._query_bus.execute(cast(object, query))  # type: ignore[arg-type]
        return GetProviderHealthOutput(
            health=health if isinstance(health, dict) else {},
            message="Provider health retrieved successfully",
        )
