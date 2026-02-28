"""Command handlers for machine operations."""

from application.base.handlers import BaseCommandHandler
from application.decorators import command_handler
from application.machine.commands import (
    CleanupMachineResourcesCommand,
    DeregisterMachineCommand,
    RegisterMachineCommand,
    UpdateMachineStatusCommand,
)
from domain.base.exceptions import DuplicateError
from domain.base.ports import ErrorHandlingPort, EventPublisherPort, LoggingPort
from domain.machine.exceptions import MachineNotFoundError
from domain.machine.repository import MachineRepository
from domain.machine.value_objects import MachineStatus


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
        """Initialize the instance."""
        super().__init__(logger, event_publisher, error_handler)
        self._machine_repository = machine_repository

    async def validate_command(self, command: UpdateMachineStatusCommand) -> None:
        """Validate machine status update command."""
        await super().validate_command(command)
        if not command.machine_id:
            raise ValueError("machine_id is required")
        if not command.status:
            raise ValueError("status is required")

    async def execute_command(self, command: UpdateMachineStatusCommand) -> None:
        """Execute machine status update command."""
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
        """Validate cleanup command."""
        await super().validate_command(command)
        if not command.machine_ids:
            raise ValueError("machine_ids is required")

    async def execute_command(self, command: CleanupMachineResourcesCommand) -> None:
        """Execute machine cleanup command."""
        for machine_id in command.machine_ids:
            machine = self._machine_repository.find_by_id(machine_id)
            if not machine:
                self.logger.warning("Machine not found for cleanup: %s", machine_id)
                continue

            machine.model_copy(update={"status": MachineStatus.TERMINATED})  # type: ignore[attr-defined]
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
        """Validate machine registration command."""
        await super().validate_command(command)
        if not command.machine_id:
            raise ValueError("machine_id is required")
        if not command.template_id:
            raise ValueError("template_id is required")

    async def execute_command(self, command: RegisterMachineCommand) -> None:
        """Execute machine registration command."""
        existing_machine = self._machine_repository.find_by_id(command.machine_id)
        if existing_machine:
            raise DuplicateError(f"Machine already registered: {command.machine_id}")

        from domain.machine.aggregate import Machine

        machine = Machine.create(  # type: ignore[attr-defined]
            machine_id=command.machine_id,
            template_id=command.template_id,
            metadata=command.metadata or {},
        )

        self._machine_repository.save(machine)


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
        """Validate machine deregistration command."""
        await super().validate_command(command)
        if not command.machine_id:
            raise ValueError("machine_id is required")

    async def execute_command(self, command: DeregisterMachineCommand) -> None:
        """Execute machine deregistration command."""
        machine = self._machine_repository.find_by_id(command.machine_id)
        if not machine:
            self.logger.warning("Machine not found for deregistration: %s", command.machine_id)
            return

        machine.model_copy(update={"status": MachineStatus.TERMINATED})  # type: ignore[attr-defined]
        self._machine_repository.save(machine)
