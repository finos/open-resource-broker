"""Command handlers for machine operations."""

from application.base.handlers import BaseCommandHandler
from application.decorators import command_handler
from application.dto.base import BaseResponse
from application.machine.commands import (
    CleanupMachineResourcesCommand,
    ConvertBatchMachineStatusCommand,
    ConvertMachineStatusCommand,
    DeregisterMachineCommand,
    RegisterMachineCommand,
    UpdateMachineStatusCommand,
    ValidateProviderStateCommand,
)
from domain.base.ports import ContainerPort, ErrorHandlingPort, EventPublisherPort, LoggingPort

# Import for type hints
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from application.services.provider_registry_service import ProviderRegistryService
from domain.machine.repository import MachineRepository
from domain.machine.value_objects import MachineStatus
from providers.base.strategy import (
    ProviderOperation,
    ProviderOperationType,
)


class ConvertMachineStatusResponse(BaseResponse):
    """Response for machine status conversion."""

    status: MachineStatus
    original_state: str
    provider_type: str


class ConvertBatchMachineStatusResponse(BaseResponse):
    """Response for batch machine status conversion."""

    statuses: list[MachineStatus]
    count: int


class ValidateProviderStateResponse(BaseResponse):
    """Response for provider state validation."""

    is_valid: bool
    provider_state: str
    provider_type: str


@command_handler(UpdateMachineStatusCommand)
class UpdateMachineStatusHandler(BaseCommandHandler[UpdateMachineStatusCommand, None]):
    """Handler for updating machine status using centralized base handler."""

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

    async def execute_command(self, command: UpdateMachineStatusCommand):
        """Execute machine status update command - error handling via base handler."""
        # Get machine
        machine = await self._machine_repository.get_by_id(command.machine_id)
        if not machine:
            raise ValueError(f"Machine not found: {command.machine_id}")

        # Update status
        machine.update_status(command.status, command.metadata)

        # Save changes and get extracted events
        await self._machine_repository.save(machine)

        # Events will be published by the base handler


@command_handler(ConvertMachineStatusCommand)
class ConvertMachineStatusCommandHandler(
    BaseCommandHandler[ConvertMachineStatusCommand, ConvertMachineStatusResponse]
):
    """Handler for converting provider-specific status to domain status."""

    def __init__(
        self,
        container: ContainerPort,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        provider_registry_service: "ProviderRegistryService",
    ) -> None:
        """Initialize with container for registry access."""
        super().__init__(logger, event_publisher, error_handler)
        self._container = container
        self._provider_registry_service = provider_registry_service

    async def validate_command(self, command: ConvertMachineStatusCommand) -> None:
        """Validate conversion command."""
        await super().validate_command(command)
        if not command.provider_state:
            raise ValueError("provider_state is required")
        if not command.provider_type:
            raise ValueError("provider_type is required")

    async def execute_command(self, command: ConvertMachineStatusCommand) -> ConvertMachineStatusResponse:
        """Execute status conversion using registry."""
        
        # Create operation for status conversion
        operation = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "provider_state": command.provider_state,
                "convert_only": True,
            }
        )
        
        # Execute via registry
        result = await registry.execute_operation(command.provider_type, operation)
        
        if result.success:
            status = result.data.get("status", MachineStatus.UNKNOWN)
            return ConvertMachineStatusResponse(
                status=status,
                original_state=command.provider_state,
                provider_type=command.provider_type,
            )
        else:
            # Fallback to unknown status
            return ConvertMachineStatusResponse(
                status=MachineStatus.UNKNOWN,
                original_state=command.provider_state,
                provider_type=command.provider_type,
            )
@command_handler(ConvertBatchMachineStatusCommand)
class ConvertBatchMachineStatusCommandHandler(
    BaseCommandHandler[ConvertBatchMachineStatusCommand, ConvertBatchMachineStatusResponse]
):
    """Handler for batch machine status conversion."""

    def __init__(
        self,
        status_converter: ConvertMachineStatusCommandHandler,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize with status converter handler."""
        super().__init__(logger, event_publisher, error_handler)
        self._status_converter = status_converter

    async def validate_command(self, command: ConvertBatchMachineStatusCommand) -> None:
        """Validate batch conversion command."""
        await super().validate_command(command)
        if not command.provider_states:
            raise ValueError("provider_states is required")

    async def execute_command(
        self, command: ConvertBatchMachineStatusCommand
    ) -> ConvertBatchMachineStatusResponse:
        """Execute batch conversion command."""
        statuses = []

        for state_info in command.provider_states:
            # Create individual conversion command
            convert_command = ConvertMachineStatusCommand(
                provider_state=state_info["state"],
                provider_type=state_info["provider_type"],
                metadata=command.metadata,
            )

            # Convert individual status
            result = await self._status_converter.execute_command(convert_command)
            statuses.append(result.status)

        return ConvertBatchMachineStatusResponse(
            success=True,
            statuses=statuses,
            count=len(statuses),
            metadata=command.metadata,
        )


@command_handler(ValidateProviderStateCommand)
class ValidateProviderStateCommandHandler(
    BaseCommandHandler[ValidateProviderStateCommand, ValidateProviderStateResponse]
):
    """Handler for validating provider state."""

    def __init__(
        self,
        container: ContainerPort,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize with container for registry access."""
        super().__init__(logger, event_publisher, error_handler)
        self._container = container

    async def validate_command(self, command: ValidateProviderStateCommand) -> None:
        """Validate provider state validation command."""
        await super().validate_command(command)
        if not command.provider_state:
            raise ValueError("provider_state is required")
        if not command.provider_type:
            raise ValueError("provider_type is required")

    async def execute_command(
        self, command: ValidateProviderStateCommand
    ) -> ValidateProviderStateResponse:
        """Execute provider state validation command."""
        try:
            # Try to convert the state - if successful, it's valid
            convert_command = ConvertMachineStatusCommand(
                provider_state=command.provider_state,
                provider_type=command.provider_type,
                metadata=command.metadata,
            )

            # Use the converter to validate
            converter = ConvertMachineStatusCommandHandler(
                self._container,
                self.logger,
                self.event_publisher,
                self.error_handler,
            )
            result = await converter.execute_command(convert_command)

            # If conversion succeeded, state is valid
            is_valid = result.success and result.status != MachineStatus.UNKNOWN

            return ValidateProviderStateResponse(
                success=True,
                is_valid=is_valid,
                provider_state=command.provider_state,
                provider_type=command.provider_type,
                metadata=command.metadata,
            )

        except Exception as e:
            return ValidateProviderStateResponse(
                success=True,
                is_valid=False,
                provider_state=command.provider_state,
                provider_type=command.provider_type,
                metadata={**command.metadata, "validation_error": str(e)},
            )


@command_handler(CleanupMachineResourcesCommand)
class CleanupMachineResourcesHandler(BaseCommandHandler[CleanupMachineResourcesCommand, None]):
    """Handler for cleaning up machine resources using centralized base handler."""

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
        if not command.machine_id:
            raise ValueError("machine_id is required")

    async def execute_command(self, command: CleanupMachineResourcesCommand):
        """Execute machine cleanup command - error handling via base handler."""
        # Get machine
        machine = await self._machine_repository.get_by_id(command.machine_id)
        if not machine:
            if self.logger:
                self.logger.warning("Machine not found for cleanup: %s", command.machine_id)
            return None

        # Perform cleanup
        machine.cleanup_resources()

        # Save changes
        await self._machine_repository.save(machine)

        return None


@command_handler(RegisterMachineCommand)
class RegisterMachineHandler(BaseCommandHandler[RegisterMachineCommand, None]):
    """Handler for registering machines using centralized base handler."""

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

    async def execute_command(self, command: RegisterMachineCommand):
        """Execute machine registration command."""
        # Check if machine already exists
        existing_machine = await self._machine_repository.get_by_id(command.machine_id)
        if existing_machine:
            raise ValueError(f"Machine already registered: {command.machine_id}")

        # Create new machine
        from domain.machine.aggregate import Machine

        machine = Machine.create(
            machine_id=command.machine_id,
            template_id=command.template_id,
            metadata=command.metadata or {},
        )

        # Save machine
        await self._machine_repository.save(machine)


@command_handler(DeregisterMachineCommand)
class DeregisterMachineHandler(BaseCommandHandler[DeregisterMachineCommand, None]):
    """Handler for deregistering machines using centralized base handler."""

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

    async def execute_command(self, command: DeregisterMachineCommand):
        """Execute machine deregistration command."""
        # Get machine
        machine = await self._machine_repository.get_by_id(command.machine_id)
        if not machine:
            if self.logger:
                self.logger.warning("Machine not found for deregistration: %s", command.machine_id)
            return None

        # Deregister machine
        machine.deregister(command.reason)

        # Save changes
        await self._machine_repository.save(machine)

        return None
