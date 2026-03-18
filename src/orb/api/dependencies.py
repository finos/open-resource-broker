"""FastAPI dependency injection integration."""

from __future__ import annotations

from typing import TypeVar

from orb.application.ports.scheduler_port import SchedulerPort
from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
from orb.application.services.orchestration.create_template import CreateTemplateOrchestrator
from orb.application.services.orchestration.delete_template import DeleteTemplateOrchestrator
from orb.application.services.orchestration.get_machine import GetMachineOrchestrator
from orb.application.services.orchestration.get_request_status import GetRequestStatusOrchestrator
from orb.application.services.orchestration.get_template import GetTemplateOrchestrator
from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
from orb.application.services.orchestration.list_return_requests import (
    ListReturnRequestsOrchestrator,
)
from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
from orb.application.services.orchestration.refresh_templates import RefreshTemplatesOrchestrator
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
from orb.application.services.orchestration.update_template import UpdateTemplateOrchestrator
from orb.application.services.orchestration.validate_template import ValidateTemplateOrchestrator
from orb.config.schemas.server_schema import ServerConfig
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.infrastructure.di.buses import CommandBus, QueryBus
from orb.infrastructure.di.container import get_container

T = TypeVar("T")


def get_di_container():
    """Get the DI container instance."""
    return get_container()


def get_service(service_type: type[T]) -> T:
    """Get services from DI container."""

    def _get_service() -> T:
        container = get_di_container()
        return container.get(service_type)

    return _get_service  # type: ignore[return-value]


def get_query_bus() -> QueryBus:
    """Get QueryBus from DI container."""
    return get_di_container().get(QueryBus)


def get_command_bus() -> CommandBus:
    """Get CommandBus from DI container."""
    return get_di_container().get(CommandBus)


def get_scheduler_strategy() -> SchedulerPort:
    """Get SchedulerPort from DI container."""
    return get_di_container().get(SchedulerPort)


def get_config_manager() -> ConfigurationPort:
    """Get ConfigurationPort from DI container."""
    return get_di_container().get(ConfigurationPort)


def get_server_config() -> ServerConfig:
    """Get ServerConfig from configuration manager."""
    config_manager = get_config_manager()
    return config_manager.get_typed(ServerConfig)  # type: ignore[arg-type]


# Orchestrator dependencies
def get_acquire_machines_orchestrator() -> AcquireMachinesOrchestrator:
    """Get AcquireMachinesOrchestrator from DI container."""
    return get_di_container().get(AcquireMachinesOrchestrator)


def get_request_status_orchestrator() -> GetRequestStatusOrchestrator:
    """Get GetRequestStatusOrchestrator from DI container."""
    return get_di_container().get(GetRequestStatusOrchestrator)


def get_list_requests_orchestrator() -> ListRequestsOrchestrator:
    """Get ListRequestsOrchestrator from DI container."""
    return get_di_container().get(ListRequestsOrchestrator)


def get_return_machines_orchestrator() -> ReturnMachinesOrchestrator:
    """Get ReturnMachinesOrchestrator from DI container."""
    return get_di_container().get(ReturnMachinesOrchestrator)


def get_cancel_request_orchestrator() -> CancelRequestOrchestrator:
    """Get CancelRequestOrchestrator from DI container."""
    return get_di_container().get(CancelRequestOrchestrator)


def get_list_machines_orchestrator() -> ListMachinesOrchestrator:
    """Get ListMachinesOrchestrator from DI container."""
    return get_di_container().get(ListMachinesOrchestrator)


def get_machine_orchestrator() -> GetMachineOrchestrator:
    """Get GetMachineOrchestrator from DI container."""
    return get_di_container().get(GetMachineOrchestrator)


def get_list_templates_orchestrator() -> ListTemplatesOrchestrator:
    """Get ListTemplatesOrchestrator from DI container."""
    return get_di_container().get(ListTemplatesOrchestrator)


def get_list_return_requests_orchestrator() -> ListReturnRequestsOrchestrator:
    """Get ListReturnRequestsOrchestrator from DI container."""
    return get_di_container().get(ListReturnRequestsOrchestrator)


def get_get_template_orchestrator() -> GetTemplateOrchestrator:
    """Get GetTemplateOrchestrator from DI container."""
    return get_di_container().get(GetTemplateOrchestrator)


def get_create_template_orchestrator() -> CreateTemplateOrchestrator:
    """Get CreateTemplateOrchestrator from DI container."""
    return get_di_container().get(CreateTemplateOrchestrator)


def get_update_template_orchestrator() -> UpdateTemplateOrchestrator:
    """Get UpdateTemplateOrchestrator from DI container."""
    return get_di_container().get(UpdateTemplateOrchestrator)


def get_delete_template_orchestrator() -> DeleteTemplateOrchestrator:
    """Get DeleteTemplateOrchestrator from DI container."""
    return get_di_container().get(DeleteTemplateOrchestrator)


def get_validate_template_orchestrator() -> ValidateTemplateOrchestrator:
    """Get ValidateTemplateOrchestrator from DI container."""
    return get_di_container().get(ValidateTemplateOrchestrator)


def get_refresh_templates_orchestrator() -> RefreshTemplatesOrchestrator:
    """Get RefreshTemplatesOrchestrator from DI container."""
    return get_di_container().get(RefreshTemplatesOrchestrator)
