"""Orchestrator for listing machines the provider has reclaimed."""

from __future__ import annotations

from orb.application.dto.queries import ListReclaimedMachinesQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    ListReclaimedMachinesInput,
    ListReclaimedMachinesOutput,
)
from orb.application.services.orchestration.list_return_requests import (
    _DEFAULT_GRACE_PERIOD,
    _SPOT_GRACE_PERIOD,
)
from orb.domain.base.ports.logging_port import LoggingPort


class ListReclaimedMachinesOrchestrator(
    OrchestratorBase[ListReclaimedMachinesInput, ListReclaimedMachinesOutput]
):
    """Orchestrator for listing machines the provider has reclaimed."""

    def __init__(
        self,
        command_bus: CommandBusPort,
        query_bus: QueryBusPort,
        logger: LoggingPort,
        default_grace_period: int = _DEFAULT_GRACE_PERIOD,
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger
        self._default_grace_period = default_grace_period

    async def execute(self, input: ListReclaimedMachinesInput) -> ListReclaimedMachinesOutput:  # type: ignore[return]
        self._logger.info(
            "ListReclaimedMachinesOrchestrator: %d machine(s) to check", len(input.identifiers)
        )
        machines = await self._dispatch(
            "ListReclaimedMachines",
            self._query_bus.execute(ListReclaimedMachinesQuery(identifiers=input.identifiers)),
        )
        return ListReclaimedMachinesOutput(
            machines=[
                {
                    "name": m.name,
                    "machine_id": m.machine_id,
                    "grace_period": _SPOT_GRACE_PERIOD
                    if m.price_type == "spot"
                    else self._default_grace_period,
                }
                for m in machines or []
            ]
        )
