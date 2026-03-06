"""Provider Strategy Command Handlers - CQRS handlers for provider strategy commands."""

import time

from orb.application.base.handlers import BaseCommandHandler
from orb.application.decorators import command_handler
from orb.application.provider.commands import (
    ExecuteProviderOperationCommand,
    RegisterProviderStrategyCommand,
    UpdateProviderHealthCommand,
)
from orb.application.services.provider_registry_service import ProviderRegistryService
from orb.domain.base.events.provider_events import (
    ProviderHealthChangedEvent,
    ProviderOperationExecutedEvent,
    ProviderStrategyRegisteredEvent,
)
from orb.domain.base.ports import ContainerPort, ErrorHandlingPort, EventPublisherPort, LoggingPort


@command_handler(ExecuteProviderOperationCommand)  # type: ignore[arg-type]
class ExecuteProviderOperationHandler(BaseCommandHandler[ExecuteProviderOperationCommand, None]):
    """Handler for executing provider operations.

    CQRS Compliance: Returns None. Result stored in command.result.
    """

    def __init__(
        self,
        container: ContainerPort,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        provider_registry_service: ProviderRegistryService,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._container = container
        self._provider_registry_service = provider_registry_service

    async def validate_command(self, command: ExecuteProviderOperationCommand) -> None:
        """Validate execute provider operation command."""
        await super().validate_command(command)
        if not command.operation:
            raise ValueError("operation is required")

    async def execute_command(self, command: ExecuteProviderOperationCommand) -> None:
        """Handle provider operation execution. Result stored in command.result."""
        operation = command.operation
        self.logger.info("Executing provider operation: %s", operation.operation_type)
        start_time = time.time()
        try:
            provider_identifier = command.strategy_override or "aws"
            result = await self._provider_registry_service.execute_operation(
                provider_identifier, operation
            )
            execution_time = (time.time() - start_time) * 1000
            event = ProviderOperationExecutedEvent(
                operation_type=operation.operation_type,
                strategy_name=provider_identifier,
                success=result.success,
                execution_time_ms=execution_time,
                error_message=result.error_message if not result.success else None,
                aggregate_id=f"operation_{operation.operation_type}_{int(execution_time)}",
                aggregate_type="provider_operation",
            )
            if self.event_publisher:
                self.event_publisher.publish(event)
            if result.success:
                self.logger.info("Operation completed successfully in %.2fms", execution_time)
            else:
                self.logger.error("Operation failed: %s", result.error_message)
            command.result = {
                "success": result.success,
                "data": result.data,
                "error_message": result.error_message,
            }
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            self.logger.error("Failed to execute provider operation: %s", str(e))
            event = ProviderOperationExecutedEvent(
                operation_type=operation.operation_type,
                strategy_name=command.strategy_override or "unknown",
                success=False,
                execution_time_ms=execution_time,
                error_message=str(e),
                aggregate_id=f"operation_{operation.operation_type}_{int(execution_time)}",
                aggregate_type="provider_operation",
            )
            if self.event_publisher:
                self.event_publisher.publish(event)
            command.result = {"success": False, "data": None, "error_message": str(e)}


@command_handler(RegisterProviderStrategyCommand)  # type: ignore[arg-type]
class RegisterProviderStrategyHandler(BaseCommandHandler[RegisterProviderStrategyCommand, None]):
    """Handler for registering new provider strategies.

    CQRS Compliance: Returns None. Result stored in command.result.
    """

    def __init__(
        self,
        container: ContainerPort,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        provider_registry_service: ProviderRegistryService,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._container = container
        self._provider_registry_service = provider_registry_service

    async def validate_command(self, command: RegisterProviderStrategyCommand) -> None:
        """Validate register provider strategy command."""
        await super().validate_command(command)
        if not command.strategy_name:
            raise ValueError("strategy_name is required")
        if not command.provider_type:
            raise ValueError("provider_type is required")

    async def execute_command(self, command: RegisterProviderStrategyCommand) -> None:
        """Handle provider strategy registration. Result stored in command.result."""
        self.logger.info("Registering provider strategy: %s", command.strategy_name)
        try:
            success = self._provider_registry_service.register_provider_strategy(
                command.provider_type.lower(), command.strategy_config
            )
            if not success:
                raise ValueError(f"Failed to register provider strategy: {command.provider_type}")
            self.logger.info("Strategy registered successfully: %s", command.strategy_name)
            event = ProviderStrategyRegisteredEvent(
                strategy_name=command.strategy_name,
                provider_type=command.provider_type,
                capabilities=None,
                priority=command.priority,
                aggregate_id=command.strategy_name,
                aggregate_type="provider_strategy",
            )
            if self.event_publisher:
                self.event_publisher.publish(event)
            command.result = {
                "strategy_name": command.strategy_name,
                "provider_type": command.provider_type,
                "status": "registered",
                "capabilities": command.capabilities or {},
            }
        except Exception as e:
            self.logger.error("Failed to register provider strategy: %s", str(e))
            raise


@command_handler(UpdateProviderHealthCommand)  # type: ignore[arg-type]
class UpdateProviderHealthHandler(BaseCommandHandler[UpdateProviderHealthCommand, None]):
    """Handler for updating provider health status.

    CQRS Compliance: Returns None. Result stored in command.result.
    """

    def __init__(
        self,
        container: ContainerPort,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        provider_registry_service: ProviderRegistryService,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._container = container
        self._provider_registry_service = provider_registry_service

    async def validate_command(self, command: UpdateProviderHealthCommand) -> None:
        """Validate update provider health command."""
        await super().validate_command(command)
        if not command.provider_name:
            raise ValueError("provider_name is required")
        if not command.health_status:
            raise ValueError("health_status is required")

    async def execute_command(self, command: UpdateProviderHealthCommand) -> None:
        """Handle provider health update. Result stored in command.result."""
        self.logger.debug("Updating health for provider: %s", command.provider_name)
        try:
            old_status = self._provider_registry_service.check_strategy_health(
                command.provider_name
            )
            new_is_healthy = (
                command.health_status.get("is_healthy", False)
                if isinstance(command.health_status, dict)
                else getattr(command.health_status, "is_healthy", False)
            )
            old_is_healthy = (
                old_status.get("is_healthy")
                if isinstance(old_status, dict)
                else getattr(old_status, "is_healthy", None)
            )
            if old_status is None or old_is_healthy != new_is_healthy:
                event = ProviderHealthChangedEvent(
                    provider_name=command.provider_name,
                    old_status=str(old_status) if old_status is not None else None,
                    new_status=str(command.health_status),
                    source=command.source,
                    aggregate_id=command.provider_name,
                    aggregate_type="provider_health",
                )
                if self.event_publisher:
                    self.event_publisher.publish(event)
                is_healthy = new_is_healthy
                self.logger.info(
                    "Provider %s is now %s",
                    command.provider_name,
                    "healthy" if is_healthy else "unhealthy",
                )
            health_data = (
                command.health_status
                if isinstance(command.health_status, dict)
                else command.health_status.model_dump()
                if hasattr(command.health_status, "model_dump")
                else {"status": str(command.health_status)}
            )
            self._provider_registry_service.update_provider_health(
                command.provider_name, health_data
            )
            command.result = {
                "provider_name": command.provider_name,
                "health_status": health_data,
                "updated_at": command.timestamp or time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception as e:
            self.logger.error("Failed to update provider health: %s", str(e))
            raise
