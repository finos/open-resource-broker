"""Command handler service registrations for dependency injection.

All command handlers are now automatically discovered and registered via
@command_handler decorators through the Handler Discovery System.
"""

from application.commands.machine_handlers import (
    CleanupMachineResourcesHandler,
    ConvertBatchMachineStatusCommandHandler,
    ConvertMachineStatusCommandHandler,
    UpdateMachineStatusHandler,
    ValidateProviderStateCommandHandler,
)
from domain.base.ports import LoggingPort
from infrastructure.di.buses import CommandBus
from infrastructure.di.container import DIContainer
from providers.base.strategy.provider_context import ProviderContext


def register_command_handler_services(container: DIContainer) -> None:
    """Register command handler services."""

    # Register machine command handlers
    _register_machine_command_handlers(container)

    # Register request command handlers
    _register_request_command_handlers(container)

    # Register template command handlers
    _register_template_command_handlers(container)

    # Register system command handlers
    _register_system_command_handlers(container)

    # Register provider command handlers
    _register_provider_command_handlers(container)

    # Register cleanup command handlers
    _register_cleanup_command_handlers(container)

    # Register CLI command handlers
    _register_cli_command_handlers(container)


def _register_machine_command_handlers(container: DIContainer) -> None:
    """Register machine-related command handlers."""

    # All machine command handlers are now automatically discovered and registered
    # via @command_handler decorators through the Handler Discovery System


def _register_request_command_handlers(container: DIContainer) -> None:
    """Register request-related command handlers."""

    # All request command handlers are now automatically discovered and registered
    # via @command_handler decorators through the Handler Discovery System


def _register_template_command_handlers(container: DIContainer) -> None:
    """Register template-related command handlers."""

    # All template command handlers are now automatically discovered and registered
    # via @command_handler decorators through the Handler Discovery System


def _register_system_command_handlers(container: DIContainer) -> None:
    """Register system-related command handlers."""

    # All system command handlers are now automatically discovered and registered
    # via @command_handler decorators through the Handler Discovery System


def _register_provider_command_handlers(container: DIContainer) -> None:
    """Register provider-related command handlers."""

    # All provider command handlers are now automatically discovered and registered
    # via @command_handler decorators through the Handler Discovery System


def _register_cleanup_command_handlers(container: DIContainer) -> None:
    """Register cleanup-related command handlers."""

    # All cleanup command handlers are now automatically discovered and registered
    # via @command_handler decorators through the Handler Discovery System


def register_command_handlers_with_bus(container: DIContainer) -> None:
    """Register command handlers with the command bus."""

    try:
        command_bus = container.get(CommandBus)
        logger = container.get(LoggingPort)

        # Get provider context for strategy handlers
        container.get(ProviderContext)

        # Register machine command handlers
        from application.machine.commands import (
            CleanupMachineResourcesCommand,
            ConvertBatchMachineStatusCommand,
            ConvertMachineStatusCommand,
            UpdateMachineStatusCommand,
            ValidateProviderStateCommand,
        )

        command_bus.register(
            ConvertMachineStatusCommand,
            container.get(ConvertMachineStatusCommandHandler),
        )

        command_bus.register(
            ConvertBatchMachineStatusCommand,
            container.get(ConvertBatchMachineStatusCommandHandler),
        )

        command_bus.register(
            ValidateProviderStateCommand,
            container.get(ValidateProviderStateCommandHandler),
        )

        command_bus.register(UpdateMachineStatusCommand, container.get(UpdateMachineStatusHandler))

        command_bus.register(
            CleanupMachineResourcesCommand,
            container.get(CleanupMachineResourcesHandler),
        )

        # Register request command handlers
        try:
            from application.commands.request_handlers import (
                CancelRequestHandler,
                CreateMachineRequestHandler,
                CreateReturnRequestHandler,
                UpdateRequestStatusHandler,
            )
            from application.dto.commands import (
                CancelRequestCommand,
                CleanupOldRequestsCommand,
                CreateRequestCommand,
                CreateReturnRequestCommand,
                UpdateRequestStatusCommand,
            )

            command_bus.register(CreateRequestCommand, container.get(CreateMachineRequestHandler))

            command_bus.register(
                CreateReturnRequestCommand, container.get(CreateReturnRequestHandler)
            )

            command_bus.register(
                UpdateRequestStatusCommand, container.get(UpdateRequestStatusHandler)
            )

            command_bus.register(CancelRequestCommand, container.get(CancelRequestHandler))

            # Register CleanupOldRequestsCommand if handler exists
            try:
                from application.commands.cleanup_handlers import (
                    CleanupOldRequestsHandler,
                )

                container.register_singleton(CleanupOldRequestsHandler)

                command_bus.register(
                    CleanupOldRequestsCommand, container.get(CleanupOldRequestsHandler)
                )
            except (ImportError, Exception) as e:
                logger.debug("CleanupOldRequestsHandler not available: %s", e)

        except Exception as e:
            logger.warning("Failed to register request command handlers with bus: %s", e)

    except Exception as e:
        logger = container.get(LoggingPort)
        logger.warning("Failed to register some command handlers: %s", e)


def _register_cli_command_handlers(container: DIContainer) -> None:
    """Register CLI-related command handlers."""

    # All CLI command handlers are now automatically discovered and registered
    # via @command_handler decorators through the Handler Discovery System
