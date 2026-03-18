"""Orchestrator for getting a single machine."""

from __future__ import annotations

from orb.application.dto.queries import GetMachineQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import GetMachineInput, GetMachineOutput
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports.logging_port import LoggingPort


class GetMachineOrchestrator(OrchestratorBase[GetMachineInput, GetMachineOutput]):
    """Orchestrator for retrieving a single machine by ID."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: GetMachineInput) -> GetMachineOutput:  # type: ignore[return]
        self._logger.info("GetMachineOrchestrator: machine_id=%s", input.machine_id)

        try:
            query = GetMachineQuery(machine_id=input.machine_id)
            result = await self._query_bus.execute(query)
            return GetMachineOutput(machine=result)
        except EntityNotFoundError:
            return GetMachineOutput(machine=None)
