"""Orchestrator for starting machines."""

from __future__ import annotations

from orb.application.dto.queries import ListMachinesQuery
from orb.application.machine.commands import UpdateMachineStatusCommand
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.provider.commands import ExecuteProviderOperationCommand
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import StartMachinesInput, StartMachinesOutput
from orb.domain.base.operations import (
    Operation as ProviderOperation,
    OperationType as ProviderOperationType,
)
from orb.domain.base.ports.logging_port import LoggingPort


class StartMachinesOrchestrator(OrchestratorBase[StartMachinesInput, StartMachinesOutput]):
    """Orchestrator for starting machines via the provider layer."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: StartMachinesInput) -> StartMachinesOutput:  # type: ignore[return]
        self._logger.info(
            "StartMachinesOrchestrator: machine_ids=%s all=%s",
            input.machine_ids,
            input.all_machines,
        )

        if input.all_machines:
            machine_dtos = await self._query_bus.execute(ListMachinesQuery(status="stopped")) or []
            machine_ids = [m.machine_id for m in machine_dtos]
        else:
            machine_ids = list(input.machine_ids)

        if not machine_ids:
            return StartMachinesOutput(
                started_machines=[],
                failed_machines=[],
                success=True,
                message="No machines to start",
            )

        provider_op = ProviderOperation(
            operation_type=ProviderOperationType.START_INSTANCES,
            parameters={"instance_ids": machine_ids},
        )
        command = ExecuteProviderOperationCommand(operation=provider_op)
        await self._command_bus.execute(command)

        if command.result and command.result.get("success"):
            start_results: dict[str, bool] = command.result.get("data", {}).get("results", {})
        else:
            start_results = {mid: False for mid in machine_ids}

        started_machines: list[str] = []
        failed_machines: list[str] = []

        for machine_id, success in start_results.items():
            if success:
                status_cmd = UpdateMachineStatusCommand(machine_id=machine_id, status="pending")
                await self._command_bus.execute(status_cmd)
                started_machines.append(machine_id)
            else:
                failed_machines.append(machine_id)

        overall_success = len(failed_machines) == 0
        message = f"Started {len(started_machines)} machines"
        if failed_machines:
            message += f", failed to start {len(failed_machines)}"

        return StartMachinesOutput(
            started_machines=started_machines,
            failed_machines=failed_machines,
            success=overall_success,
            message=message,
        )
