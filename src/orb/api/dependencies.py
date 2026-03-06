"""FastAPI dependency injection integration."""

from typing import TypeVar

from orb.config.schemas.server_schema import ServerConfig
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.infrastructure.di.buses import CommandBus, QueryBus
from orb.infrastructure.di.container import get_container

T = TypeVar("T")


def get_di_container():
    """Get the DI container instance."""
    return get_container()


def get_service(service_type: type[T]) -> T:
    """
    Get services from DI container.

    Args:
        service_type: Type of service to retrieve

    Returns:
        Service instance from DI container
    """

    def _get_service() -> T:
        container = get_di_container()
        return container.get(service_type)

    return _get_service  # type: ignore[return-value]


def get_query_bus() -> QueryBus:
    """Get QueryBus from DI container."""
    container = get_di_container()
    return container.get(QueryBus)


def get_command_bus() -> CommandBus:
    """Get CommandBus from DI container."""
    container = get_di_container()
    return container.get(CommandBus)


def get_config_manager() -> ConfigurationPort:
    """Get ConfigurationPort from DI container."""
    container = get_di_container()
    return container.get(ConfigurationPort)


def get_server_config() -> ServerConfig:
    """Get ServerConfig from configuration manager."""
    config_manager = get_config_manager()
    return config_manager.get_typed(ServerConfig)  # type: ignore[arg-type]


# API Handler Dependencies
def get_template_handler():
    """Get template API handler from DI container."""

    container = get_di_container()
    from orb.api.handlers.get_available_templates_handler import (
        GetAvailableTemplatesRESTHandler,
    )

    return container.get(GetAvailableTemplatesRESTHandler)


def get_request_machines_handler():
    """Get request machines API handler from DI container."""

    container = get_di_container()
    from orb.api.handlers.request_machines_handler import RequestMachinesRESTHandler

    return container.get(RequestMachinesRESTHandler)


def get_request_status_handler():
    """Get request status API handler from DI container."""

    container = get_di_container()
    from orb.api.handlers.get_request_status_handler import GetRequestStatusRESTHandler

    return container.get(GetRequestStatusRESTHandler)



def get_return_machines_handler():
    """Get return machines API handler from DI container."""

    container = get_di_container()
    from orb.api.handlers.request_return_machines_handler import RequestReturnMachinesRESTHandler

    return container.get(RequestReturnMachinesRESTHandler)
