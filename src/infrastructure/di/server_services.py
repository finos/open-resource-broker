"""Server service registrations for dependency injection."""

from config.schemas.server_schema import ServerConfig
from domain.base.ports.configuration_port import ConfigurationPort
from infrastructure.di.container import DIContainer
from infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


def register_server_services(container: DIContainer) -> None:
    """
    Register server services conditionally based on configuration.

    Only registers server components if server.enabled=true in configuration.
    This follows the established pattern of conditional service registration.

    Args:
        container: DI container instance
    """
    try:
        # Get configuration manager
        config_manager = container.get(ConfigurationPort)
        server_config = config_manager.get_typed(ServerConfig)

        # Only register server services if enabled
        if server_config.enabled:
            logger.info("Server enabled - registering FastAPI services")
            _register_fastapi_services(container, server_config)
            _register_api_handlers(container)
            logger.info("FastAPI services registered successfully")
        else:
            logger.debug("Server disabled - skipping FastAPI service registration")

    except Exception as e:
        logger.warning(f"Failed to register server services: {e}")
        # Don't raise - server services are optional


def _register_fastapi_services(container: DIContainer, server_config: ServerConfig) -> None:
    """Register FastAPI core services."""
    from fastapi import FastAPI

    from api.server import create_fastapi_app

    # Register FastAPI app as singleton
    container.register_singleton(FastAPI, lambda c: create_fastapi_app(server_config))

    # Register server config for easy access
    container.register_singleton(ServerConfig, lambda c: server_config)


def _register_api_handlers(container: DIContainer) -> None:
    """Register API handlers with dependency injection."""
    try:
        # Register template handler with constructor injection
        from api.handlers.get_available_templates_handler import (
            GetAvailableTemplatesRESTHandler,
        )
        from domain.base.ports import SchedulerPort
        from infrastructure.di.buses import CommandBus, QueryBus
        from monitoring.metrics import MetricsCollector

        if not container.is_registered(GetAvailableTemplatesRESTHandler):
            container.register_singleton(
                GetAvailableTemplatesRESTHandler,
                lambda c: GetAvailableTemplatesRESTHandler(
                    query_bus=c.get(QueryBus),
                    command_bus=c.get(CommandBus),
                    scheduler_strategy=c.get(SchedulerPort),
                    metrics=(
                        c.get(MetricsCollector) if c.is_registered(MetricsCollector) else None
                    ),
                ),
            )

    except ImportError:
        logger.debug("Template handler not available for registration")

    try:
        # Register request machines handler
        from api.handlers.request_machines_handler import RequestMachinesRESTHandler

        if not container.is_registered(RequestMachinesRESTHandler):
            container.register_singleton(
                RequestMachinesRESTHandler,
                lambda c: RequestMachinesRESTHandler(
                    query_bus=c.get(QueryBus),
                    command_bus=c.get(CommandBus),
                    scheduler_strategy=c.get(SchedulerPort),
                    logger=c.get(LoggingPort),
                    error_handler=(
                        c.get(ErrorHandlingPort) if c.is_registered(ErrorHandlingPort) else None
                    ),
                    metrics=(
                        c.get(MetricsCollector) if c.is_registered(MetricsCollector) else None
                    ),
                ),
            )

    except ImportError:
        logger.debug("Request machines handler not available for registration")

    try:
        # Register request status handler
        from api.handlers.get_request_status_handler import (
            GetRequestStatusRESTHandler,
        )
        from domain.base.ports import ErrorHandlingPort, LoggingPort

        if not container.is_registered(GetRequestStatusRESTHandler):
            container.register_singleton(
                GetRequestStatusRESTHandler,
                lambda c: GetRequestStatusRESTHandler(
                    query_bus=c.get(QueryBus),
                    command_bus=c.get(CommandBus),
                    scheduler_strategy=c.get(SchedulerPort),
                    logger=c.get(LoggingPort),
                    error_handler=(
                        c.get(ErrorHandlingPort) if c.is_registered(ErrorHandlingPort) else None
                    ),
                    metrics=(
                        c.get(MetricsCollector) if c.is_registered(MetricsCollector) else None
                    ),
                ),
            )

    except ImportError:
        logger.debug("Request status handler not available for registration")

    try:
        # Register return requests handler
        from api.handlers.get_return_requests_handler import (
            GetReturnRequestsRESTHandler,
        )

        if not container.is_registered(GetReturnRequestsRESTHandler):
            container.register_singleton(
                GetReturnRequestsRESTHandler,
                lambda c: GetReturnRequestsRESTHandler(
                    query_bus=c.get(QueryBus),
                    command_bus=c.get(CommandBus),
                    scheduler_strategy=c.get(SchedulerPort),
                    logger=c.get(LoggingPort),
                    error_handler=(
                        c.get(ErrorHandlingPort) if c.is_registered(ErrorHandlingPort) else None
                    ),
                    metrics=(
                        c.get(MetricsCollector) if c.is_registered(MetricsCollector) else None
                    ),
                ),
            )

    except ImportError:
        logger.debug("Return requests handler not available for registration")

    try:
        # Register return machines handler
        from api.handlers.request_return_machines_handler import (
            RequestReturnMachinesRESTHandler,
        )

        if not container.is_registered(RequestReturnMachinesRESTHandler):
            container.register_singleton(
                RequestReturnMachinesRESTHandler,
                lambda c: RequestReturnMachinesRESTHandler(
                    query_bus=c.get(QueryBus),
                    command_bus=c.get(CommandBus),
                    scheduler_strategy=c.get(SchedulerPort),
                    logger=c.get(LoggingPort),
                    error_handler=(
                        c.get(ErrorHandlingPort) if c.is_registered(ErrorHandlingPort) else None
                    ),
                    metrics=(
                        c.get(MetricsCollector) if c.is_registered(MetricsCollector) else None
                    ),
                ),
            )

    except ImportError:
        logger.debug("Return machines handler not available for registration")
