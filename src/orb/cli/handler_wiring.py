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
