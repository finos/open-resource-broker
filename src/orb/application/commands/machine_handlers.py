"""Command handlers for machine operations."""

from orb.application.base.handlers import BaseCommandHandler
from orb.application.decorators import command_handler
from orb.application.machine.commands import (
    CleanupMachineResourcesCommand,
    DeregisterMachineCommand,
    RegisterMachineCommand,
    UpdateMachineProviderDataCommand,
    UpdateMachineStatusCommand,
)
from orb.domain.base.exceptions import DuplicateError
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
        machine.update_status(
            MachineStatus.from_str(command.status)
            if isinstance(command.status, str)
            else command.status
        )  # type: ignore[arg-type]
        self._machine_repository.save(machine)


@command_handler(CleanupMachineResourcesCommand)  # type: ignore[arg-type]
class CleanupMachineResourcesHandler(BaseCommandHandler[CleanupMachineResourcesCommand, None]):
    """Handler for cleaning up machine resources."""

    def __init__(
        self,
        machine_repository: MachineRepository,
        event_publisher: EventPublisherPort,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._machine_repository = machine_repository

    async def validate_command(self, command: CleanupMachineResourcesCommand) -> None:
        await super().validate_command(command)
        if not command.machine_ids:
            raise ValueError("machine_ids is required")

    async def execute_command(self, command: CleanupMachineResourcesCommand):
        for machine_id in command.machine_ids:
            machine = self._machine_repository.find_by_id(machine_id)
            if not machine:
                if self.logger:
                    self.logger.warning("Machine not found for cleanup: %s", machine_id)
                continue
            machine = machine.model_copy(  # type: ignore[attr-defined]
                update={
                    "status": MachineStatus.TERMINATED,
                    "status_reason": "Terminated",
                }
            )
            self._machine_repository.save(machine)


@command_handler(RegisterMachineCommand)  # type: ignore[arg-type]
class RegisterMachineHandler(BaseCommandHandler[RegisterMachineCommand, None]):
    """Handler for registering machines."""

    def __init__(
        self,
        machine_repository: MachineRepository,
        event_publisher: EventPublisherPort,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._machine_repository = machine_repository

    async def validate_command(self, command: RegisterMachineCommand) -> None:
        await super().validate_command(command)
        if not command.machine_id:
            raise ValueError("machine_id is required")
        if not command.template_id:
            raise ValueError("template_id is required")

    async def execute_command(self, command: RegisterMachineCommand):
        existing_machine = self._machine_repository.find_by_id(command.machine_id)
        if existing_machine:
            raise DuplicateError(f"Machine already registered: {command.machine_id}")
        from orb.domain.machine.aggregate import Machine

        machine = Machine.create(  # type: ignore[attr-defined]
            machine_id=command.machine_id,
            template_id=command.template_id,
            metadata=command.metadata or {},
        )
        self._machine_repository.save(machine)


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


@command_handler(DeregisterMachineCommand)  # type: ignore[arg-type]
class DeregisterMachineHandler(BaseCommandHandler[DeregisterMachineCommand, None]):
    """Handler for deregistering machines."""

    def __init__(
        self,
        machine_repository: MachineRepository,
        event_publisher: EventPublisherPort,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._machine_repository = machine_repository

    async def validate_command(self, command: DeregisterMachineCommand) -> None:
        await super().validate_command(command)
        if not command.machine_id:
            raise ValueError("machine_id is required")

    async def execute_command(self, command: DeregisterMachineCommand):
        machine = self._machine_repository.find_by_id(command.machine_id)
        if not machine:
            if self.logger:
                self.logger.warning("Machine not found for deregistration: %s", command.machine_id)
            return None
        machine = machine.model_copy(  # type: ignore[attr-defined]
            update={
                "status": MachineStatus.TERMINATED,
                "status_reason": "Terminated",
            }
        )
        self._machine_repository.save(machine)
        return None
