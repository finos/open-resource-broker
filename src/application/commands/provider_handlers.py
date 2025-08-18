"""Provider Strategy Command Handlers - CQRS handlers for provider strategy commands.

This module implements command handlers for provider strategy operations,
integrating the existing provider strategy ecosystem with the CQRS architecture.
"""

import time
from typing import Any, Dict

from application.base.handlers import BaseCommandHandler
from application.decorators import command_handler
from application.provider.commands import (
    ConfigureProviderStrategyCommand,
    ExecuteProviderOperationCommand,
    RegisterProviderStrategyCommand,
    SelectProviderStrategyCommand,
    UpdateProviderHealthCommand,
)
from domain.base.events.provider_events import (
    ProviderHealthChangedEvent,
    ProviderOperationExecutedEvent,
    ProviderStrategyRegisteredEvent,
    ProviderStrategySelectedEvent,
)
from domain.base.ports import ErrorHandlingPort, EventPublisherPort, LoggingPort
from providers.base.strategy import (
    ProviderContext,
    ProviderResult,
    SelectionPolicy,
    SelectorFactory,
)


@command_handler(SelectProviderStrategyCommand)
class SelectProviderStrategyHandler(
    BaseCommandHandler[SelectProviderStrategyCommand, Dict[str, Any]]
):
    """Handler for selecting optimal provider strategy."""

    def __init__(
        self,
        provider_context: ProviderContext,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ):
        """Initialize the instance."""
        super().__init__(logger, event_publisher, error_handler)
        self._provider_context = provider_context

    async def validate_command(self, command: SelectProviderStrategyCommand) -> None:
        """Validate select provider strategy command."""
        await super().validate_command(command)
        if not command.operation_type:
            raise ValueError("operation_type is required")

    async def execute_command(self, command: SelectProviderStrategyCommand) -> Dict[str, Any]:
        """Handle provider strategy selection command."""
        self.logger.info("Selecting provider strategy for operation: %s", command.operation_type)

        try:
            # Use existing provider context to select strategy
            selector = SelectorFactory.create_selector(
                SelectionPolicy.CAPABILITY_BASED,
                self.logger,  # Use capability-based selection
            )

            # Get available strategies from context
            available_strategies = self._provider_context.get_available_strategies()

            if not available_strategies:
                raise ValueError("No provider strategies available")

            # Select optimal strategy based on criteria
            selection_result = selector.select(
                available_strategies, command.selection_criteria, command.operation_type
            )

            if not selection_result.selected_strategy:
                raise ValueError("No suitable provider strategy found")

            # Publish strategy selection event
            event = ProviderStrategySelectedEvent(
                strategy_name=selection_result.selected_strategy.name,
                operation_type=command.operation_type,
                selection_criteria=command.selection_criteria,
                selection_reason=selection_result.selection_reason,
            )
            self.event_publisher.publish(event)

            self.logger.info("Selected strategy: %s", selection_result.selected_strategy.name)

            return {
                "selected_strategy": selection_result.selected_strategy.name,
                "selection_reason": selection_result.selection_reason,
                "confidence_score": selection_result.confidence_score,
                "alternatives": [s.name for s in selection_result.alternative_strategies],
            }

        except Exception as e:
            self.logger.error("Failed to select provider strategy: %s", str(e))
            raise


@command_handler(ExecuteProviderOperationCommand)
class ExecuteProviderOperationHandler(
    BaseCommandHandler[ExecuteProviderOperationCommand, ProviderResult]
):
    """Handler for executing provider operations through strategy pattern."""

    def __init__(
        self,
        provider_context: ProviderContext,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, event_publisher, error_handler)
        self._provider_context = provider_context

    async def validate_command(self, command: ExecuteProviderOperationCommand) -> None:
        """Validate execute provider operation command."""
        await super().validate_command(command)
        if not command.operation:
            raise ValueError("operation is required")

    async def execute_command(self, command: ExecuteProviderOperationCommand) -> ProviderResult:
        """Handle provider operation execution command."""
        operation = command.operation
        self.logger.info("Executing provider operation: %s", operation.operation_type)

        start_time = time.time()

        try:
            # Execute operation through provider context
            if command.strategy_override:
                # Use specific strategy if override provided
                result = self._provider_context.execute_with_strategy(
                    command.strategy_override, operation
                )
            else:
                # Use context's strategy selection
                result = await self._provider_context.execute_operation(operation)

            execution_time = (time.time() - start_time) * 1000  # Convert to milliseconds

            # Publish operation execution event
            event = ProviderOperationExecutedEvent(
                operation_type=operation.operation_type,
                strategy_name=self._provider_context.current_strategy_name,
                success=result.success,
                execution_time_ms=execution_time,
                error_message=result.error_message if not result.success else None,
            )
            self.event_publisher.publish(event)

            if result.success:
                self.logger.info("Operation completed successfully in %sms", execution_time:.2f)
            else:
                self.logger.error("Operation failed: %s", result.error_message)

            return result

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            self.logger.error("Failed to execute provider operation: %s", str(e))

            # Publish failure event
            event = ProviderOperationExecutedEvent(
                operation_type=operation.operation_type,
                strategy_name=self._provider_context.current_strategy_name or "unknown",
                success=False,
                execution_time_ms=execution_time,
                error_message=str(e),
            )
            self.event_publisher.publish(event)

            # Return error result instead of raising
            return ProviderResult.error_result(error_message=str(e), error_code="EXECUTION_FAILED")


@command_handler(RegisterProviderStrategyCommand)
class RegisterProviderStrategyHandler(
    BaseCommandHandler[RegisterProviderStrategyCommand, Dict[str, Any]]
):
    """Handler for registering new provider strategies."""

    def __init__(
        self,
        provider_context: ProviderContext,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, event_publisher, error_handler)
        self._provider_context = provider_context

    async def validate_command(self, command: RegisterProviderStrategyCommand) -> None:
        """Validate register provider strategy command."""
        await super().validate_command(command)
        if not command.strategy_name:
            raise ValueError("strategy_name is required")
        if not command.provider_type:
            raise ValueError("provider_type is required")

    async def execute_command(self, command: RegisterProviderStrategyCommand) -> Dict[str, Any]:
        """Handle provider strategy registration command."""
        self.logger.info("Registering provider strategy: %s", command.strategy_name)

        try:
            # Use provider registry to create strategy
            from infrastructure.registry.provider_registry import get_provider_registry

            registry = get_provider_registry()

            # Create a mock provider config for strategy creation
            from dataclasses import dataclass
            from typing import Any, Dict

            @dataclass
            class MockProviderConfig:
                """Mock provider configuration for testing and strategy creation."""

                type: str
                name: str
                config: Dict[str, Any]

            provider_config = MockProviderConfig(
                type=command.provider_type.lower(),
                name=command.strategy_name,
                config=command.strategy_config,
            )

            strategy = registry.create_strategy(command.provider_type.lower(), provider_config)

            # Register strategy with context
            self._provider_context.register_strategy(strategy, command.strategy_name)

            # Publish registration event
            event = ProviderStrategyRegisteredEvent(
                strategy_name=command.strategy_name,
                provider_type=command.provider_type,
                capabilities=command.capabilities or {},
                priority=command.priority,
            )
            self.event_publisher.publish(event)

            self.logger.info("Successfully registered strategy: %s", command.strategy_name)

            return {
                "strategy_name": command.strategy_name,
                "provider_type": command.provider_type,
                "status": "registered",
                "capabilities": strategy.get_capabilities().model_dump(),
            }

        except Exception as e:
            self.logger.error("Failed to register provider strategy: %s", str(e))
            raise


@command_handler(UpdateProviderHealthCommand)
class UpdateProviderHealthHandler(BaseCommandHandler[UpdateProviderHealthCommand, Dict[str, Any]]):
    """Handler for updating provider health status."""

    def __init__(
        self,
        provider_context: ProviderContext,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, event_publisher, error_handler)
        self._provider_context = provider_context

    async def validate_command(self, command: UpdateProviderHealthCommand) -> None:
        """Validate update provider health command."""
        await super().validate_command(command)
        if not command.provider_name:
            raise ValueError("provider_name is required")
        if not command.health_status:
            raise ValueError("health_status is required")

    async def execute_command(self, command: UpdateProviderHealthCommand) -> Dict[str, Any]:
        """Handle provider health status update command."""
        self.logger.debug("Updating health for provider: %s", command.provider_name)

        try:
            # Get current health status for comparison
            old_status = self._provider_context.check_strategy_health(command.provider_name)

            # Update health status in context
            self._provider_context.update_provider_health(
                command.provider_name, command.health_status
            )

            # Publish health change event if status changed
            if old_status is None or old_status.is_healthy != command.health_status.is_healthy:
                event = ProviderHealthChangedEvent(
                    provider_name=command.provider_name,
                    old_status=old_status,
                    new_status=command.health_status,
                    source=command.source,
                )
                self.event_publisher.publish(event)

                status_change = "healthy" if command.health_status.is_healthy else "unhealthy"
                self.logger.info("Provider %s is now %s", command.provider_name, status_change)

            return {
                "provider_name": command.provider_name,
                "health_status": command.health_status.model_dump(),
                "updated_at": command.timestamp or time.strftime("%Y-%m-%d %H:%M:%S"),
            }

        except Exception as e:
            self.logger.error("Failed to update provider health: %s", str(e))
            raise


@command_handler(ConfigureProviderStrategyCommand)
class ConfigureProviderStrategyHandler(
    BaseCommandHandler[ConfigureProviderStrategyCommand, Dict[str, Any]]
):
    """Handler for configuring provider strategy policies."""

    def __init__(
        self,
        provider_context: ProviderContext,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, event_publisher, error_handler)
        self._provider_context = provider_context

    async def validate_command(self, command: ConfigureProviderStrategyCommand) -> None:
        """Validate configure provider strategy command."""
        await super().validate_command(command)
        # Configuration commands can have optional parameters, so minimal validation

    async def execute_command(self, command: ConfigureProviderStrategyCommand) -> Dict[str, Any]:
        """Handle provider strategy configuration command."""
        self.logger.info("Configuring provider strategy policies")

        try:
            # Update provider context configuration
            config_updates = {
                "default_selection_policy": command.default_selection_policy,
                "selection_criteria": command.selection_criteria,
                "fallback_strategies": command.fallback_strategies or [],
                "health_check_interval": command.health_check_interval,
                "circuit_breaker_config": command.circuit_breaker_config or {},
            }

            self._provider_context.update_configuration(config_updates)

            self.logger.info("Provider strategy configuration updated successfully")

            return {
                "status": "configured",
                "configuration": config_updates,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

        except Exception as e:
            self.logger.error("Failed to configure provider strategy: %s", str(e))
            raise
