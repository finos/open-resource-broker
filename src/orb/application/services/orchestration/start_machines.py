"""Orchestrator for starting machines."""

from __future__ import annotations

from orb.application.dto.queries import ListMachinesQuery
from orb.application.machine.commands import UpdateMachineStatusCommand
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.provider.commands import ExecuteProviderOperationCommand
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import StartMachinesInput, StartMachinesOutput
from orb.application.services.provider_registry_service import ProviderRegistryService
from orb.domain.base.operations import (
    Operation as ProviderOperation,
    OperationType as ProviderOperationType,
)
from orb.domain.base.ports.logging_port import LoggingPort


class StartMachinesOrchestrator(OrchestratorBase[StartMachinesInput, StartMachinesOutput]):
    """Orchestrator for starting machines via the provider layer."""

    def __init__(
        self,
        command_bus: CommandBusPort,
        query_bus: QueryBusPort,
        logger: LoggingPort,
        provider_registry_service: ProviderRegistryService,
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger
        self._provider_registry_service = provider_registry_service

    async def execute(self, input: StartMachinesInput) -> StartMachinesOutput:  # type: ignore[return]
        self._logger.info(
            "StartMachinesOrchestrator: machine_ids=%s all=%s",
            input.machine_ids,
            input.all_machines,
        )

        if input.all_machines:
            machine_dtos = (
                await self._query_bus.execute(
                    ListMachinesQuery(
                        status="stopped",
                        provider_name=input.provider_name,
                        provider_type=input.provider_type,
                        filter_expressions=input.filter_expressions,
                    )
                )
                or []
            )
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

        # Resolve the effective provider identifier so the command handler can
        # route the operation to the correct provider strategy.  Preference
        # order: explicit name > explicit type > active provider from registry.
        if input.provider_name:
            strategy_override = input.provider_name
        elif input.provider_type:
            strategy_override = input.provider_type
        else:
            try:
                selection = self._provider_registry_service.select_active_provider()
                strategy_override = selection.provider_name
            except Exception as exc:
                self._logger.error(
                    "StartMachinesOrchestrator: cannot resolve active provider: %s", exc
                )
                return StartMachinesOutput(
                    started_machines=[],
                    failed_machines=list(machine_ids),
                    success=False,
                    message=f"Cannot resolve active provider: {exc}",
                )

        provider_op = ProviderOperation(
            operation_type=ProviderOperationType.START_INSTANCES,
            parameters={"instance_ids": machine_ids},
        )
        command = ExecuteProviderOperationCommand(
            operation=provider_op, strategy_override=strategy_override
        )
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
