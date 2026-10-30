"""Command handlers for machine operations."""

from orb.application.base.handlers import BaseCommandHandler
from orb.application.decorators import command_handler
from orb.application.machine.commands import (
    CleanupMachineResourcesCommand,
    UpdateMachineProviderDataCommand,
    UpdateMachineStatusCommand,
)
from orb.application.services.provider_registry_service import ProviderRegistryService
from orb.domain.base.exceptions import ApplicationError
from orb.domain.base.operations import Operation, OperationType
from orb.domain.base.ports import ErrorHandlingPort, EventPublisherPort, LoggingPort
from orb.domain.machine.exceptions import MachineNotFoundError
from orb.domain.machine.repository import MachineRepository
from orb.domain.machine.value_objects import MachineStatus


@command_handler(UpdateMachineStatusCommand)  # type: ignore[arg-type]
class UpdateMachineStatusHandler(BaseCommandHandler[UpdateMachineStatusCommand, None]):
    """Handler for updating machine status."""

    def __init__(
        self,
        machine_repository: MachineRepository,
        event_publisher: EventPublisherPort,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._machine_repository = machine_repository

    async def validate_command(self, command: UpdateMachineStatusCommand) -> None:
        await super().validate_command(command)
        if not command.machine_id:
            raise ValueError("machine_id is required")
        if not command.status:
            raise ValueError("status is required")

    async def execute_command(self, command: UpdateMachineStatusCommand):
        machine = self._machine_repository.find_by_id(command.machine_id)
        if not machine:
            raise MachineNotFoundError(command.machine_id)
        new_status = (
            MachineStatus.from_str(command.status)
            if isinstance(command.status, str)
            else command.status
        )
        # Machine is an immutable aggregate: update_status returns a NEW machine
        # (with the status change and its MachineStatusChangedEvent) rather than
        # mutating in place. Persist the returned instance, not the original.
        updated = machine.update_status(new_status)  # type: ignore[arg-type]
        events = self._machine_repository.save(updated)
        for event in events or []:
            self.event_publisher.publish(event)  # type: ignore[union-attr]


@command_handler(CleanupMachineResourcesCommand)  # type: ignore[arg-type]
class CleanupMachineResourcesHandler(BaseCommandHandler[CleanupMachineResourcesCommand, None]):
    """Handler for cleaning up machine resources."""

    def __init__(
        self,
        machine_repository: MachineRepository,
        provider_registry_service: ProviderRegistryService,
        event_publisher: EventPublisherPort,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._machine_repository = machine_repository
        self._provider_registry_service = provider_registry_service

    async def validate_command(self, command: CleanupMachineResourcesCommand) -> None:
        await super().validate_command(command)
        if not command.machine_ids:
            raise ValueError("machine_ids is required")

    async def execute_command(self, command: CleanupMachineResourcesCommand):
        """Tear down provider-side resources for an explicit list of machines.

        For each machine the handler resolves its owning provider and dispatches
        a ``CLEANUP_MACHINE_RESOURCES`` operation through the provider registry so
        the provider strategy performs the real teardown (volumes, network
        interfaces, ...). Only after a successful teardown is the machine marked
        terminal and persisted.

        ``force_cleanup`` relaxes the guarantees: machines that are already
        terminal are cleaned up anyway, and a per-machine provider failure is
        logged and the machine is still terminalised rather than aborting the
        batch. Without the flag, a machine already in a terminal state is skipped
        and provider failures leave the machine untouched. Failures are collected
        and, when not forcing, surfaced after the batch completes.
        """
        failure_count = 0

        for machine_id in command.machine_ids:
            machine = self._machine_repository.find_by_id(machine_id)
            if not machine:
                if self.logger:
                    self.logger.warning("Machine not found for cleanup: %s", machine_id)
                continue

            if machine.status.is_terminal and not command.force_cleanup:
                if self.logger:
                    self.logger.debug("Machine %s already terminal; skipping cleanup", machine_id)
                continue

            operation = Operation(
                operation_type=OperationType.CLEANUP_MACHINE_RESOURCES,
                parameters={
                    "machine": machine,
                    "instance_ids": [machine_id],
                    "force_cleanup": command.force_cleanup,
                },
                context={"machine_id": str(machine_id)},
            )

            try:
                result = await self._provider_registry_service.execute_operation(
                    machine.provider_name, operation
                )
            except Exception as exc:  # provider unreachable / registry error
                # Machine IDs and provider text go to the log only; the raised
                # error carries a bounded summary.
                if self.logger:
                    self.logger.error("Cleanup dispatch failed for %s: %s", machine_id, exc)
                failure_count += 1
                if not command.force_cleanup:
                    continue
                result = None

            if result is not None and not result.success:
                if self.logger:
                    self.logger.error(
                        "Provider cleanup failed for %s: %s", machine_id, result.error_message
                    )
                failure_count += 1
                if not command.force_cleanup:
                    continue

            machine = machine.model_copy(  # type: ignore[attr-defined]
                update={
                    "status": MachineStatus.TERMINATED,
                    "status_reason": "Terminated",
                }
            )
            self._machine_repository.save(machine)

        if failure_count and not command.force_cleanup:
            raise ApplicationError(
                f"{failure_count} of {len(command.machine_ids)} machines could not be cleaned up",
                error_code="MACHINE_CLEANUP_FAILED",
                details={
                    "failed_count": failure_count,
                    "requested_count": len(command.machine_ids),
                },
            )


@command_handler(UpdateMachineProviderDataCommand)  # type: ignore[arg-type]
class UpdateMachineProviderDataHandler(BaseCommandHandler[UpdateMachineProviderDataCommand, None]):
    """Merge *updates* into a machine's provider_data without clobbering other keys."""

    def __init__(
        self,
        machine_repository: MachineRepository,
        event_publisher: EventPublisherPort,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._machine_repository = machine_repository

    async def validate_command(self, command: UpdateMachineProviderDataCommand) -> None:
        await super().validate_command(command)
        if not command.machine_id:
            raise ValueError("machine_id is required")

    async def execute_command(self, command: UpdateMachineProviderDataCommand) -> None:
        machine = self._machine_repository.find_by_id(command.machine_id)
        if not machine:
            raise MachineNotFoundError(command.machine_id)
        merged = {**machine.provider_data, **command.updates}
        updated = machine.set_provider_data(merged)
        self._machine_repository.save(updated)
