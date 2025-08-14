"""Command handlers for machine operations."""

from typing import List

from src.application.base.handlers import BaseCommandHandler
from src.application.decorators import command_handler
from src.application.dto.base import BaseResponse
from src.application.machine.commands import (
    CleanupMachineResourcesCommand,
    ConvertBatchMachineStatusCommand,
    ConvertMachineStatusCommand,
    DeregisterMachineCommand,
    RegisterMachineCommand,
    UpdateMachineStatusCommand,
    ValidateProviderStateCommand,
)
from src.domain.base.ports import ErrorHandlingPort, EventPublisherPort, LoggingPort
from src.domain.machine.repository import MachineRepository
from src.domain.machine.value_objects import MachineStatus
from src.providers.base.strategy import (
    ProviderContext,
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

    statuses: List[MachineStatus]
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
    ):
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
        return None  # No response needed for this command


@command_handler(ConvertMachineStatusCommand)
class ConvertMachineStatusCommandHandler(
    BaseCommandHandler[ConvertMachineStatusCommand, ConvertMachineStatusResponse]
):
    """Handler for converting provider-specific status to domain status."""

    def __init__(
        self,
        provider_context: ProviderContext,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ):
        """Initialize with provider context."""
        super().__init__(logger, event_publisher, error_handler)
        self._provider_context = provider_context

    async def validate_command(self, command: ConvertMachineStatusCommand) -> None:
        """Validate machine status conversion command."""
        await super().validate_command(command)
        if not command.provider_state:
            raise ValueError("provider_state is required")
        if not command.provider_type:
            raise ValueError("provider_type is required")

    async def execute_command(
        self, command: ConvertMachineStatusCommand
    ) -> ConvertMachineStatusResponse:
        """Execute machine status conversion command."""
        try:
            # Use provider strategy pattern for conversion
            domain_status = await self._convert_using_provider_strategy(
                command.provider_state, command.provider_type
            )

            return ConvertMachineStatusResponse(
                success=True,
                status=domain_status,
                original_state=command.provider_state,
                provider_type=command.provider_type,
                metadata=command.metadata,
            )

        except Exception as e:
            # Fallback to basic conversion
            fallback_status = self._fallback_conversion(command.provider_state)

            return ConvertMachineStatusResponse(
                success=True,  # Still successful with fallback
                status=fallback_status,
                original_state=command.provider_state,
                provider_type=command.provider_type,
                metadata={**command.metadata, "used_fallback": True, "error": str(e)},
            )

    async def _convert_using_provider_strategy(
        self, provider_state: str, provider_type: str
    ) -> MachineStatus:
        """Convert using provider strategy pattern."""
        # Set the appropriate provider strategy
        if not self._provider_context.set_strategy(provider_type):
            raise ValueError(f"Unsupported provider type: {provider_type}")

        # Create provider operation for status conversion
        operation = ProviderOperation(
            # Using health check as proxy for status mapping
            operation_type=ProviderOperationType.HEALTH_CHECK,
            parameters={"provider_state": provider_state, "conversion_request": True},
        )

        # Execute operation (this would be extended to support status conversion)
        result = self._provider_context.execute_operation(operation)

        if result.success:
            # Extract status from result (implementation depends on provider strategy)
            return self._extract_status_from_result(result, provider_state)
        else:
            raise Exception(f"Provider operation failed: {result.error_message}")

    def _extract_status_from_result(self, result, provider_state: str) -> MachineStatus:
        """Extract MachineStatus from provider result."""
        # This is a simplified implementation
        # In practice, each provider strategy would handle status mapping
        return self._fallback_conversion(provider_state)

    def _fallback_conversion(self, provider_state: str) -> MachineStatus:
        """Fallback conversion when provider strategy is not available."""
        state_mapping = {
            "running": MachineStatus.RUNNING,
            "stopped": MachineStatus.STOPPED,
            "pending": MachineStatus.PENDING,
            "stopping": MachineStatus.STOPPING,
            "terminated": MachineStatus.TERMINATED,
            "shutting-down": MachineStatus.STOPPING,
        }

        normalized_state = provider_state.lower().replace("_", "-")
        return state_mapping.get(normalized_state, MachineStatus.UNKNOWN)


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
    ):
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
        provider_context: ProviderContext,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ):
        """Initialize with provider context."""
        super().__init__(logger, event_publisher, error_handler)
        self._provider_context = provider_context

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
                self._provider_context,
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
    ):
        super().__init__(logger, event_publisher, error_handler)
        self._machine_repository = machine_repository

    async def validate_command(self, command: CleanupMachineResourcesCommand) -> None:
        """Validate cleanup command."""
        await super().validate_command(command)
        if not command.machine_id:
            raise ValueError("machine_id is required")

    async def execute_command(self, command: CleanupMachineResourcesCommand) -> None:
        """Execute machine cleanup command - error handling via base handler."""
        # Get machine
        machine = await self._machine_repository.get_by_id(command.machine_id)
        if not machine:
            if self.logger:
                self.logger.warning(f"Machine not found for cleanup: {command.machine_id}")
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
    ):
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
        # Check if machine already exists
        existing_machine = await self._machine_repository.get_by_id(command.machine_id)
        if existing_machine:
            raise ValueError(f"Machine already registered: {command.machine_id}")

        # Create new machine
        from src.domain.machine.aggregate import Machine

        machine = Machine.create(
            machine_id=command.machine_id,
            template_id=command.template_id,
            metadata=command.metadata or {},
        )

        # Save machine
        await self._machine_repository.save(machine)

        return None


@command_handler(DeregisterMachineCommand)
class DeregisterMachineHandler(BaseCommandHandler[DeregisterMachineCommand, None]):
    """Handler for deregistering machines using centralized base handler."""

    def __init__(
        self,
        machine_repository: MachineRepository,
        event_publisher: EventPublisherPort,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, event_publisher, error_handler)
        self._machine_repository = machine_repository

    async def validate_command(self, command: DeregisterMachineCommand) -> None:
        """Validate machine deregistration command."""
        await super().validate_command(command)
        if not command.machine_id:
            raise ValueError("machine_id is required")

    async def execute_command(self, command: DeregisterMachineCommand) -> None:
        """Execute machine deregistration command."""
        # Get machine
        machine = await self._machine_repository.get_by_id(command.machine_id)
        if not machine:
            if self.logger:
                self.logger.warning(f"Machine not found for deregistration: {command.machine_id}")
            return None

        # Deregister machine
        machine.deregister(command.reason)

        # Save changes
        await self._machine_repository.save(machine)

        return None
