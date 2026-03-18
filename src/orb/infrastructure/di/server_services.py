"""Server service registrations for dependency injection."""

from orb.config.schemas.server_schema import ServerConfig
from orb.infrastructure.di.container import DIContainer
from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


def register_server_services(container: DIContainer) -> None:
    """
    Register server services conditionally based on configuration.

    Only registers server components if server.enabled=true in configuration.
    This follows the established pattern of conditional service registration.

    Args:
        container: DI container instance
    """
    from orb.config.managers.configuration_manager import ConfigurationManager

    config_manager = container.get(ConfigurationManager)
    server_config = config_manager.get_typed(ServerConfig)

    # Only register server services if enabled
    if server_config.enabled:
        logger.info("Server enabled - registering FastAPI services")
        _register_fastapi_services(container, server_config)
        _register_orchestrators(container)
        logger.info("FastAPI services registered successfully")
    else:
        logger.debug("Server disabled - skipping FastAPI service registration")


def _register_fastapi_services(container: DIContainer, server_config: ServerConfig) -> None:
    """Register FastAPI core services."""
    from fastapi import FastAPI

    from orb.api.server import create_fastapi_app

    # Register FastAPI app as singleton
    container.register_singleton(FastAPI, lambda c: create_fastapi_app(server_config))

    # Register server config for easy access
    container.register_singleton(ServerConfig, lambda c: server_config)


def _register_orchestrators(container: DIContainer) -> None:
    """Register orchestrators with dependency injection."""
    try:
        from orb.domain.base.ports.logging_port import LoggingPort
        from orb.infrastructure.di.buses import CommandBus, QueryBus
    except ImportError as e:
        logger.debug("Orchestrator dependencies not available: %s", e)
        return

    try:
        from orb.application.services.orchestration.acquire_machines import (
            AcquireMachinesOrchestrator,
        )

        if not container.is_registered(AcquireMachinesOrchestrator):
            container.register_singleton(
                AcquireMachinesOrchestrator,
                lambda c: AcquireMachinesOrchestrator(
                    command_bus=c.get(CommandBus),
                    query_bus=c.get(QueryBus),
                    logger=c.get(LoggingPort),
                ),
            )
    except ImportError:
        logger.debug("AcquireMachinesOrchestrator not available for registration")

    try:
        from orb.application.services.orchestration.get_request_status import (
            GetRequestStatusOrchestrator,
        )

        if not container.is_registered(GetRequestStatusOrchestrator):
            container.register_singleton(
                GetRequestStatusOrchestrator,
                lambda c: GetRequestStatusOrchestrator(
                    command_bus=c.get(CommandBus),
                    query_bus=c.get(QueryBus),
                    logger=c.get(LoggingPort),
                ),
            )
    except ImportError:
        logger.debug("GetRequestStatusOrchestrator not available for registration")

    try:
        from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator

        if not container.is_registered(ListRequestsOrchestrator):
            container.register_singleton(
                ListRequestsOrchestrator,
                lambda c: ListRequestsOrchestrator(
                    command_bus=c.get(CommandBus),
                    query_bus=c.get(QueryBus),
                    logger=c.get(LoggingPort),
                ),
            )
    except ImportError:
        logger.debug("ListRequestsOrchestrator not available for registration")

    try:
        from orb.application.services.orchestration.return_machines import (
            ReturnMachinesOrchestrator,
        )

        if not container.is_registered(ReturnMachinesOrchestrator):
            container.register_singleton(
                ReturnMachinesOrchestrator,
                lambda c: ReturnMachinesOrchestrator(
                    command_bus=c.get(CommandBus),
                    query_bus=c.get(QueryBus),
                    logger=c.get(LoggingPort),
                ),
            )
    except ImportError:
        logger.debug("ReturnMachinesOrchestrator not available for registration")

    try:
        from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator

        if not container.is_registered(CancelRequestOrchestrator):
            container.register_singleton(
                CancelRequestOrchestrator,
                lambda c: CancelRequestOrchestrator(
                    command_bus=c.get(CommandBus),
                    query_bus=c.get(QueryBus),
                    logger=c.get(LoggingPort),
                ),
            )
    except ImportError:
        logger.debug("CancelRequestOrchestrator not available for registration")

    try:
        from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator

        if not container.is_registered(ListMachinesOrchestrator):
            container.register_singleton(
                ListMachinesOrchestrator,
                lambda c: ListMachinesOrchestrator(
                    command_bus=c.get(CommandBus),
                    query_bus=c.get(QueryBus),
                    logger=c.get(LoggingPort),
                ),
            )
    except ImportError:
        logger.debug("ListMachinesOrchestrator not available for registration")

    try:
        from orb.application.services.orchestration.get_machine import GetMachineOrchestrator

        if not container.is_registered(GetMachineOrchestrator):
            container.register_singleton(
                GetMachineOrchestrator,
                lambda c: GetMachineOrchestrator(
                    command_bus=c.get(CommandBus),
                    query_bus=c.get(QueryBus),
                    logger=c.get(LoggingPort),
                ),
            )
    except ImportError:
        logger.debug("GetMachineOrchestrator not available for registration")

    try:
        from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator

        if not container.is_registered(ListTemplatesOrchestrator):
            container.register_singleton(
                ListTemplatesOrchestrator,
                lambda c: ListTemplatesOrchestrator(
                    command_bus=c.get(CommandBus),
                    query_bus=c.get(QueryBus),
                    logger=c.get(LoggingPort),
                ),
            )
    except ImportError:
        logger.debug("ListTemplatesOrchestrator not available for registration")

    try:
        from orb.application.services.orchestration.list_return_requests import (
            ListReturnRequestsOrchestrator,
        )

        if not container.is_registered(ListReturnRequestsOrchestrator):
            container.register_singleton(
                ListReturnRequestsOrchestrator,
                lambda c: ListReturnRequestsOrchestrator(
                    command_bus=c.get(CommandBus),
                    query_bus=c.get(QueryBus),
                    logger=c.get(LoggingPort),
                ),
            )
    except ImportError:
        logger.debug("ListReturnRequestsOrchestrator not available for registration")
