"""Orchestrator for stopping machines."""

from __future__ import annotations

from typing import cast

from orb.application.dto.queries import ListMachinesQuery
from orb.application.machine.commands import UpdateMachineStatusCommand
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.provider.commands import ExecuteProviderOperationCommand
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import StopMachinesInput, StopMachinesOutput
from orb.domain.base.operations import (
    Operation as ProviderOperation,
    OperationType as ProviderOperationType,
)
from orb.domain.base.ports.logging_port import LoggingPort


class StopMachinesOrchestrator(OrchestratorBase[StopMachinesInput, StopMachinesOutput]):
    """Orchestrator for stopping machines via the provider layer."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: StopMachinesInput) -> StopMachinesOutput:  # type: ignore[return]
        self._logger.info(
            "StopMachinesOrchestrator: machine_ids=%s all=%s force=%s",
            input.machine_ids,
            input.all_machines,
            input.force,
        )

        if input.all_machines:
            machine_dtos = await self._query_bus.execute(ListMachinesQuery(status="running")) or []
            machine_ids = [m.machine_id for m in machine_dtos]
        else:
            machine_ids = list(input.machine_ids)

        if not machine_ids:
            return StopMachinesOutput(
                stopped_machines=[],
                failed_machines=[],
                success=True,
                message="No machines to stop",
            )

        operation = ProviderOperation(
            operation_type=ProviderOperationType.STOP_INSTANCES,
            parameters={"instance_ids": machine_ids},
        )
        command = ExecuteProviderOperationCommand(operation=operation)
        await self._command_bus.execute(cast(object, command))  # type: ignore[arg-type]

        if command.result and command.result.get("success"):
            stop_results: dict[str, bool] = command.result.get("data", {}).get("results", {})
        else:
            stop_results = {mid: False for mid in machine_ids}

        stopped_machines: list[str] = []
        failed_machines: list[str] = []

        for machine_id, success in stop_results.items():
            if success:
                status_cmd = UpdateMachineStatusCommand(machine_id=machine_id, status="stopping")
                await self._command_bus.execute(cast(object, status_cmd))  # type: ignore[arg-type]
                stopped_machines.append(machine_id)
            else:
                failed_machines.append(machine_id)

        overall_success = len(failed_machines) == 0
        message = f"Stopped {len(stopped_machines)} machines"
        if failed_machines:
            message += f", failed to stop {len(failed_machines)}"

        return StopMachinesOutput(
            stopped_machines=stopped_machines,
            failed_machines=failed_machines,
            success=overall_success,
            message=message,
        )
