"""Command handlers for machine operations."""

from typing import TYPE_CHECKING

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
from domain.base.exceptions import DuplicateError
from domain.base.operations import (
    Operation as ProviderOperation,
    OperationType as ProviderOperationType,
)
from domain.base.ports import ContainerPort, ErrorHandlingPort, EventPublisherPort, LoggingPort
from domain.machine.exceptions import MachineNotFoundError

if TYPE_CHECKING:
    from application.services.provider_registry_service import ProviderRegistryService
from domain.machine.repository import MachineRepository
from domain.machine.value_objects import MachineStatus


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


@command_handler(ConvertMachineStatusCommand)  # type: ignore[arg-type]
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
        super().__init__(logger, event_publisher, error_handler)
        self._container = container
        self._provider_registry_service = provider_registry_service

    async def validate_command(self, command: ConvertMachineStatusCommand) -> None:
        await super().validate_command(command)
        if not command.provider_state:
            raise ValueError("provider_state is required")
        if not command.provider_type:
            raise ValueError("provider_type is required")

    async def execute_command(
        self, command: ConvertMachineStatusCommand
    ) -> ConvertMachineStatusResponse:
        operation = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={"provider_state": command.provider_state, "convert_only": True},
        )
        result = await self._provider_registry_service.execute_operation(
            command.provider_type, operation
        )
        if result.success:
            status = result.data.get("status", MachineStatus.UNKNOWN)
            return ConvertMachineStatusResponse(
                status=status,
                original_state=command.provider_state,
                provider_type=command.provider_type,
            )
        return ConvertMachineStatusResponse(
            status=MachineStatus.UNKNOWN,
            original_state=command.provider_state,
            provider_type=command.provider_type,
        )


@command_handler(ConvertBatchMachineStatusCommand)  # type: ignore[arg-type]
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
        super().__init__(logger, event_publisher, error_handler)
        self._status_converter = status_converter

    async def validate_command(self, command: ConvertBatchMachineStatusCommand) -> None:
        await super().validate_command(command)
        if not command.provider_states:
            raise ValueError("provider_states is required")

    async def execute_command(
        self, command: ConvertBatchMachineStatusCommand
    ) -> ConvertBatchMachineStatusResponse:
        statuses = []
        for state_info in command.provider_states:
            convert_command = ConvertMachineStatusCommand(
                provider_state=state_info["state"],
                provider_type=state_info["provider_type"],
                metadata=command.metadata,
            )
            result = await self._status_converter.execute_command(convert_command)
            statuses.append(result.status)
        return ConvertBatchMachineStatusResponse(
            success=True,
            statuses=statuses,
            count=len(statuses),
            metadata=command.metadata,
        )


@command_handler(ValidateProviderStateCommand)  # type: ignore[arg-type]
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
        provider_registry_service: "ProviderRegistryService",
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._container = container
        self._provider_registry_service = provider_registry_service

    async def validate_command(self, command: ValidateProviderStateCommand) -> None:
        await super().validate_command(command)
        if not command.provider_state:
            raise ValueError("provider_state is required")
        if not command.provider_type:
            raise ValueError("provider_type is required")

    async def execute_command(
        self, command: ValidateProviderStateCommand
    ) -> ValidateProviderStateResponse:
        try:
            convert_command = ConvertMachineStatusCommand(
                provider_state=command.provider_state,
                provider_type=command.provider_type,
                metadata=command.metadata,
            )
            converter = ConvertMachineStatusCommandHandler(
                self._container,
                self.logger,  # type: ignore[arg-type]
                self.event_publisher,  # type: ignore[arg-type]
                self.error_handler,  # type: ignore[arg-type]
                self._provider_registry_service,
            )
            result = await converter.execute_command(convert_command)
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
        await super().validate_command(command)
        if not command.machine_id:
            raise ValueError("machine_id is required")
        if not command.template_id:
            raise ValueError("template_id is required")

    async def execute_command(self, command: RegisterMachineCommand):
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
        await super().validate_command(command)
        if not command.machine_id:
            raise ValueError("machine_id is required")

    async def execute_command(self, command: DeregisterMachineCommand):
        machine = self._machine_repository.find_by_id(command.machine_id)
        if not machine:
            if self.logger:
                self.logger.warning("Machine not found for deregistration: %s", command.machine_id)
            return None
        machine.model_copy(update={"status": MachineStatus.TERMINATED})  # type: ignore[attr-defined]
        self._machine_repository.save(machine)
        return None
