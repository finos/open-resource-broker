"""Orchestrator for returning machines."""

from __future__ import annotations

from orb.application.dto.commands import CreateReturnRequestCommand
from orb.application.dto.queries import ListMachinesQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import ReturnMachinesInput, ReturnMachinesOutput
from orb.domain.base.ports.logging_port import LoggingPort


class ReturnMachinesOrchestrator(OrchestratorBase[ReturnMachinesInput, ReturnMachinesOutput]):
    """Orchestrator for returning machines to the provider."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: ReturnMachinesInput) -> ReturnMachinesOutput:  # type: ignore[return]
        self._logger.info(
            "ReturnMachinesOrchestrator: machines=%s all=%s force=%s",
            input.machine_ids,
            input.all_machines,
            input.force,
        )

        if input.all_machines:
            machine_dtos = (
                await self._query_bus.execute(ListMachinesQuery(all_resources=True)) or []
            )
            machine_ids = [dto.machine_id for dto in machine_dtos]
            if not machine_ids:
                self._logger.warning(
                    "ReturnMachinesOrchestrator: --all requested but no active machines found"
                )
                return ReturnMachinesOutput(
                    request_id=None,
                    status="no_machines",
                    raw={"status": "no_machines", "message": "No active machines found"},
                )
        else:
            machine_ids = list(input.machine_ids)

        command = CreateReturnRequestCommand(
            machine_ids=machine_ids,
            force_return=input.force,
        )
        await self._command_bus.execute(command)

        if not command.created_request_ids:
            skipped = command.skipped_machines or []
            self._logger.warning(
                "CreateReturnRequestCommand produced no request IDs; skipped=%s", skipped
            )
            return ReturnMachinesOutput(
                request_id=None,
                status="no_op",
                raw={"status": "no_op", "skipped_machines": skipped},
            )
        request_id = command.created_request_ids[0]
        status = "pending"
        return ReturnMachinesOutput(
            request_id=request_id,
            status=status,
            raw={"request_id": request_id, "status": status},
        )
