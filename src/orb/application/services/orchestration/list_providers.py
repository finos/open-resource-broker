"""Orchestrator for listing available providers."""

from __future__ import annotations

from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.provider.queries import ListAvailableProvidersQuery
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import ListProvidersInput, ListProvidersOutput
from orb.domain.base.ports.logging_port import LoggingPort


class ListProvidersOrchestrator(OrchestratorBase[ListProvidersInput, ListProvidersOutput]):
    """Orchestrator for listing available provider strategies."""

    def __init__(self, query_bus: QueryBusPort, logger: LoggingPort) -> None:
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: ListProvidersInput) -> ListProvidersOutput:  # type: ignore[return]
        self._logger.info("ListProvidersOrchestrator: provider_name=%s", input.provider_name)
        query = ListAvailableProvidersQuery(provider_name=input.provider_name)
        result = await self._query_bus.execute(query)
        if isinstance(result, dict):
            return ListProvidersOutput(
                providers=result.get("providers", []),
                count=result.get("count", 0),
                selection_policy=result.get("selection_policy", ""),
                message=result.get("message", ""),
            )
        return ListProvidersOutput()
