"""Orchestrator for listing machines."""

from __future__ import annotations

from orb.application.dto.queries import ListMachinesQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import ListMachinesInput, ListMachinesOutput
from orb.domain.base.ports.logging_port import LoggingPort


class ListMachinesOrchestrator(OrchestratorBase[ListMachinesInput, ListMachinesOutput]):
    """Orchestrator for listing machines."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: ListMachinesInput) -> ListMachinesOutput:  # type: ignore[return]
        self._logger.info(
            "ListMachinesOrchestrator: status=%s provider=%s request_id=%s limit=%d",
            input.status,
            input.provider_name,
            input.request_id,
            input.limit,
        )

        query = ListMachinesQuery(
            status=input.status,
            provider_name=input.provider_name,
            request_id=input.request_id,
            limit=input.limit,
            offset=input.offset,
        )
        results = await self._query_bus.execute(query)
        machines = list(results or [])
        return ListMachinesOutput(machines=machines, count=len(machines))
