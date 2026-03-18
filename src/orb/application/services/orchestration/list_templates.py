"""Orchestrator for listing templates."""

from __future__ import annotations

from orb.application.dto.queries import ListTemplatesQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import ListTemplatesInput, ListTemplatesOutput
from orb.domain.base.ports.logging_port import LoggingPort


class ListTemplatesOrchestrator(OrchestratorBase[ListTemplatesInput, ListTemplatesOutput]):
    """Orchestrator for listing available templates."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: ListTemplatesInput) -> ListTemplatesOutput:  # type: ignore[return]
        self._logger.info(
            "ListTemplatesOrchestrator: active_only=%s provider=%s limit=%d",
            input.active_only,
            input.provider_name,
            input.limit,
        )

        query = ListTemplatesQuery(
            active_only=input.active_only,
            provider_name=input.provider_name,
            provider_api=input.provider_api,
            limit=input.limit,
        )
        results = await self._query_bus.execute(query)
        return ListTemplatesOutput(templates=list(results or []))
