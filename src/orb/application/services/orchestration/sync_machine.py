"""Orchestrator for refreshing a single machine from the provider.

Mirrors the per-request ``GET /requests/{id}/status`` sync pattern: load
the machine and its parent request, ask ``MachineSyncService`` for a
provider refresh, persist any changes, and return the up-to-date DTO.

Used by ``GET /api/v1/machines/{machine_id}/status`` and the UI's
drawer "Sync" button. Bounded cost (one DescribeInstances per call),
unlike a list-wide ``sync=true`` which scales linearly with page size.
"""

from __future__ import annotations

from orb.application.machine.dto import MachineDTO
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.machine_sync_service import MachineSyncService
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import SyncMachineInput, SyncMachineOutput
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports.logging_port import LoggingPort


class SyncMachineOrchestrator(OrchestratorBase[SyncMachineInput, SyncMachineOutput]):
    """Refresh a single machine from the provider and persist the result."""

    def __init__(
        self,
        command_bus: CommandBusPort,
        query_bus: QueryBusPort,
        uow_factory: UnitOfWorkFactory,
        machine_sync_service: MachineSyncService,
        logger: LoggingPort,
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._uow_factory = uow_factory
        self._machine_sync_service = machine_sync_service
        self._logger = logger

    async def execute(self, input: SyncMachineInput) -> SyncMachineOutput:  # type: ignore[return]
        self._logger.info("SyncMachineOrchestrator: machine_id=%s", input.machine_id)

        machine = None
        request = None
        with self._uow_factory.create_unit_of_work() as uow:
            machine = uow.machines.get_by_id(input.machine_id)
            if machine is None:
                return SyncMachineOutput(machine=None, synced=False, error="machine_not_found")
            if machine.request_id:
                request = uow.requests.get_by_id(machine.request_id)

        if request is None:
            dto = MachineDTO.from_domain(machine)
            return SyncMachineOutput(
                machine=dto,
                synced=False,
                error="no_parent_request",
            )

        try:
            provider_machines, _ = await self._machine_sync_service.fetch_provider_machines(
                request, [machine]
            )
        except Exception as exc:
            self._logger.warning(
                "Provider fetch failed for machine %s: %s", input.machine_id, exc
            )
            dto = MachineDTO.from_domain(machine)
            return SyncMachineOutput(machine=dto, synced=False, error=str(exc))

        if not provider_machines:
            dto = MachineDTO.from_domain(machine)
            return SyncMachineOutput(
                machine=dto,
                synced=False,
                error="provider_returned_no_data",
            )

        try:
            synced_machines, _ = await self._machine_sync_service.sync_machines_with_provider(
                request, [machine], provider_machines
            )
        except Exception as exc:
            self._logger.warning(
                "Provider sync persist failed for machine %s: %s", input.machine_id, exc
            )
            dto = MachineDTO.from_domain(machine)
            return SyncMachineOutput(machine=dto, synced=False, error=str(exc))

        # Pick the synced machine that matches the requested id (sync may
        # return >1 entry when machines share a request).
        refreshed = machine
        if synced_machines:
            for sm in synced_machines:
                if sm.machine_id == machine.machine_id:
                    refreshed = sm
                    break

        return SyncMachineOutput(machine=MachineDTO.from_domain(refreshed), synced=True)
