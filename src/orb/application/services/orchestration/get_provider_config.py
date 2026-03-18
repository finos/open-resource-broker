"""Orchestrator for getting provider configuration."""

from __future__ import annotations

from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.queries.system import GetProviderConfigQuery
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    GetProviderConfigInput,
    GetProviderConfigOutput,
)
from orb.domain.base.ports.logging_port import LoggingPort


class GetProviderConfigOrchestrator(
    OrchestratorBase[GetProviderConfigInput, GetProviderConfigOutput]
):
    """Orchestrator for retrieving provider configuration."""

    def __init__(self, query_bus: QueryBusPort, logger: LoggingPort) -> None:
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: GetProviderConfigInput) -> GetProviderConfigOutput:  # type: ignore[return]
        self._logger.info("GetProviderConfigOrchestrator: executing")
        query = GetProviderConfigQuery()
        config_dto = await self._query_bus.execute(query)
        # Convert ProviderConfigDTO to dict
        config_dict = config_dto.model_dump() if hasattr(config_dto, "model_dump") else {}
        return GetProviderConfigOutput(
            config=config_dict,
            message="Provider configuration retrieved successfully",
        )
