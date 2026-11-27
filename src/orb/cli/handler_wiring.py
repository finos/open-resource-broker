"""Registration of CLI handler classes in the dependency injection container.

Each ``register_<group>_handlers`` function registers the handler classes of one
command group. They are called lazily by :func:`orb.cli.registry.bind` on the
current container, so a command only pays for the group it runs, and each is
safe to call repeatedly: a class that is already registered is left alone.

Collaborators are resolved when a handler class is first built. A collaborator
that the handler only reaches inside a guarded block is passed as a provider
(``Callable[[], T]``) instead, so a failure to resolve it surfaces at the same
place it always did.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

T = TypeVar("T")


def _register(container: Any, cls: type[T], factory: Callable[[Any], T]) -> None:
    """Register ``cls`` as a singleton built by ``factory`` unless already registered."""
    if not container.is_registered(cls):
        container.register_singleton(cls, factory)


def register_request_handlers(container: Any) -> None:
    """Register the request command and query handler classes."""
    from orb.application.ports.query_bus_port import QueryBusPort
    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
    from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
    from orb.application.services.orchestration.confirm_terminated import (
        ConfirmTerminatedOrchestrator,
    )
    from orb.application.services.orchestration.get_request_status import (
        GetRequestStatusOrchestrator,
    )
    from orb.application.services.orchestration.list_reclaimed_machines import (
        ListReclaimedMachinesOrchestrator,
    )
    from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
    from orb.application.services.orchestration.list_return_requests import (
        ListReturnRequestsOrchestrator,
    )
    from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
    from orb.application.services.orchestration.watch_request_status import (
        WatchRequestStatusOrchestrator,
    )
    from orb.domain.base import UnitOfWorkFactory
    from orb.interface.request_command_handlers import RequestWriteHandlers
    from orb.interface.request_query_handlers import RequestReadHandlers
    from orb.interface.response_formatting_service import ResponseFormattingService

    _register(
        container,
        RequestWriteHandlers,
        lambda c: RequestWriteHandlers(
            acquire_orchestrator=c.get(AcquireMachinesOrchestrator),
            return_orchestrator=c.get(ReturnMachinesOrchestrator),
            cancel_orchestrator=c.get(CancelRequestOrchestrator),
            confirm_orchestrator=c.get(ConfirmTerminatedOrchestrator),
            uow_factory=c.get(UnitOfWorkFactory),
            formatter=c.get(ResponseFormattingService),
            scheduler=c.get(SchedulerPort),
        ),
    )
    _register(
        container,
        RequestReadHandlers,
        lambda c: RequestReadHandlers(
            status_orchestrator=c.get(GetRequestStatusOrchestrator),
            list_requests_orchestrator=c.get(ListRequestsOrchestrator),
            list_return_requests_orchestrator=c.get(ListReturnRequestsOrchestrator),
            reclaimed_orchestrator=c.get(ListReclaimedMachinesOrchestrator),
            watch_orchestrator=c.get(WatchRequestStatusOrchestrator),
            query_bus=c.get(QueryBusPort),
            formatter=c.get(ResponseFormattingService),
            scheduler=c.get(SchedulerPort),
        ),
    )


def register_machine_handlers(container: Any) -> None:
    """Register the machine handler class."""
    from orb.application.ports.query_bus_port import QueryBusPort
    from orb.application.services.orchestration.get_machine import GetMachineOrchestrator
    from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
    from orb.application.services.orchestration.start_machines import StartMachinesOrchestrator
    from orb.application.services.orchestration.stop_machines import StopMachinesOrchestrator
    from orb.interface.machine_command_handlers import MachineHandlers
    from orb.interface.response_formatting_service import ResponseFormattingService

    _register(
        container,
        MachineHandlers,
        lambda c: MachineHandlers(
            list_orchestrator=c.get(ListMachinesOrchestrator),
            get_orchestrator=c.get(GetMachineOrchestrator),
            stop_orchestrator=c.get(StopMachinesOrchestrator),
            start_orchestrator=c.get(StartMachinesOrchestrator),
            query_bus=c.get(QueryBusPort),
            formatter=c.get(ResponseFormattingService),
        ),
    )


def register_template_handlers(container: Any) -> None:
    """Register the template handler classes, including ``templates generate``."""
    from orb.application.ports.query_bus_port import QueryBusPort
    from orb.application.services.orchestration.create_template import CreateTemplateOrchestrator
    from orb.application.services.orchestration.delete_template import DeleteTemplateOrchestrator
    from orb.application.services.orchestration.get_template import GetTemplateOrchestrator
    from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
    from orb.application.services.orchestration.refresh_templates import (
        RefreshTemplatesOrchestrator,
    )
    from orb.application.services.orchestration.update_template import UpdateTemplateOrchestrator
    from orb.application.services.orchestration.validate_template import (
        ValidateTemplateOrchestrator,
    )
    from orb.application.services.template_generation_service import TemplateGenerationService
    from orb.domain.base.ports.console_port import ConsolePort
    from orb.interface.response_formatting_service import ResponseFormattingService
    from orb.interface.template_command_handlers import (
        TemplateReadHandlers,
        TemplateWriteHandlers,
    )
    from orb.interface.templates_generate_handler import TemplatesGenerateHandler

    _register(
        container,
        TemplateReadHandlers,
        lambda c: TemplateReadHandlers(
            list_orchestrator=c.get(ListTemplatesOrchestrator),
            get_orchestrator=c.get(GetTemplateOrchestrator),
            validate_orchestrator=c.get(ValidateTemplateOrchestrator),
            query_bus=c.get(QueryBusPort),
            formatter=c.get(ResponseFormattingService),
            console_provider=lambda: c.get(ConsolePort),
        ),
    )
    _register(
        container,
        TemplateWriteHandlers,
        lambda c: TemplateWriteHandlers(
            create_orchestrator=c.get(CreateTemplateOrchestrator),
            update_orchestrator=c.get(UpdateTemplateOrchestrator),
            delete_orchestrator=c.get(DeleteTemplateOrchestrator),
            refresh_orchestrator=c.get(RefreshTemplatesOrchestrator),
            formatter=c.get(ResponseFormattingService),
        ),
    )
    _register(
        container,
        TemplatesGenerateHandler,
        lambda c: TemplatesGenerateHandler(
            template_service_provider=lambda: c.get(TemplateGenerationService),
            console_provider=lambda: c.get(ConsolePort),
        ),
    )


def register_config_handlers(container: Any) -> None:
    """Register the config handler class."""
    from orb.application.ports.command_bus_port import CommandBusPort
    from orb.application.ports.query_bus_port import QueryBusPort
    from orb.interface.config_command_handlers import ConfigHandlers
    from orb.interface.response_formatting_service import ResponseFormattingService

    _register(
        container,
        ConfigHandlers,
        lambda c: ConfigHandlers(
            query_bus=c.get(QueryBusPort),
            command_bus=c.get(CommandBusPort),
            formatter=c.get(ResponseFormattingService),
        ),
    )


def register_scheduler_handlers(container: Any) -> None:
    """Register the scheduler handler class."""
    from orb.application.ports.query_bus_port import QueryBusPort
    from orb.application.services.orchestration.get_scheduler_config import (
        GetSchedulerConfigOrchestrator,
    )
    from orb.application.services.orchestration.list_scheduler_strategies import (
        ListSchedulerStrategiesOrchestrator,
    )
    from orb.interface.response_formatting_service import ResponseFormattingService
    from orb.interface.scheduler_command_handlers import SchedulerHandlers

    _register(
        container,
        SchedulerHandlers,
        lambda c: SchedulerHandlers(
            list_orchestrator=c.get(ListSchedulerStrategiesOrchestrator),
            config_orchestrator=c.get(GetSchedulerConfigOrchestrator),
            query_bus=c.get(QueryBusPort),
            formatter=c.get(ResponseFormattingService),
        ),
    )


def register_storage_handlers(container: Any) -> None:
    """Register the storage handler class."""
    from orb.application.ports.query_bus_port import QueryBusPort
    from orb.application.services.orchestration.get_storage_config import (
        GetStorageConfigOrchestrator,
    )
    from orb.application.services.orchestration.list_storage_strategies import (
        ListStorageStrategiesOrchestrator,
    )
    from orb.config.manager import ConfigurationManager
    from orb.interface.response_formatting_service import ResponseFormattingService
    from orb.interface.storage_command_handlers import StorageHandlers

    _register(
        container,
        StorageHandlers,
        lambda c: StorageHandlers(
            list_orchestrator=c.get(ListStorageStrategiesOrchestrator),
            config_orchestrator=c.get(GetStorageConfigOrchestrator),
            query_bus_provider=lambda: c.get(QueryBusPort),
            config_manager_provider=lambda: c.get(ConfigurationManager),
            formatter=c.get(ResponseFormattingService),
        ),
    )
