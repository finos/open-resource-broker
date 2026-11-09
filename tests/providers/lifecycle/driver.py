"""Drives acquire / sync / cancel / return through the real container.

Only the cloud client is faked (see ``cloud.py``). The command and query buses,
handlers, status services and storage are the production ones. After every step
the driver reads what ORB stored and runs the oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orb.application.decorators import get_command_handler_for_type
from orb.application.dto.commands import (
    CreateRequestCommand,
    CreateReturnRequestCommand,
)
from orb.application.dto.queries import SyncAndGetRequestQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.ports.scheduler_port import SchedulerPort
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.clock import Clock
from orb.domain.request.request_types import RequestStatus
from tests.providers.lifecycle import oracle
from tests.providers.lifecycle.clocking import FakeClock
from tests.providers.lifecycle.cloud import Cloud
from tests.providers.lifecycle.oracle import RequestView, Scenario


@dataclass
class Handle:
    """One acquire request under test."""

    request_id: str
    requested: int
    dry_run: bool = False


def _status(value: Any) -> RequestStatus:
    return value if isinstance(value, RequestStatus) else RequestStatus(str(value))


class Lifecycle:
    def __init__(self, cell: Any, cloud: Cloud, clock: FakeClock, container: Any) -> None:
        self.cell = cell
        self.cloud = cloud
        self.clock = clock
        self.container = container
        self.command_bus = container.get(CommandBusPort)
        self.query_bus = container.get(QueryBusPort)
        self.scheduler = container.get(SchedulerPort)
        self.uow_factory = container.get(UnitOfWorkFactory)
        self.scenario = Scenario(cell=cell.name, requested=0)
        self.handles: dict[str, Handle] = {}
        self._last_dto: dict[str, Any] = {}
        self._assert_production_classes()

    # ---- wiring -------------------------------------------------------------------

    def _assert_production_classes(self) -> None:
        """The run goes through the real buses, not a stand-in."""
        from orb.infrastructure.di.buses import CommandBus, QueryBus

        assert type(self.command_bus) is CommandBus, type(self.command_bus)
        assert type(self.query_bus) is QueryBus, type(self.query_bus)

    def advance_clock(self, seconds: float) -> None:
        self.clock.advance(seconds)

    # ---- steps --------------------------------------------------------------------

    async def acquire(self, count: int, *, dry_run: bool = False) -> Handle:
        calls_before = len(self.cloud.call_log)
        command = CreateRequestCommand(
            template_id=self.cell.template_id, requested_count=count, dry_run=dry_run
        )
        await self.command_bus.execute(command)
        request_id = command.created_request_id
        assert request_id, "CreateRequestCommand did not record a request id"
        handle = Handle(request_id, count, dry_run)
        self.handles[request_id] = handle
        self.scenario.requested = count
        self.scenario.dry_run = dry_run
        self.scenario.request_ids.add(request_id)
        if not dry_run:
            assert len(self.cloud.call_log) > calls_before, "create reached no cloud call"
        self._check(handle, phase="created")
        return handle

    async def sync(self, handle: Handle, *, check: bool = True) -> Any:
        dto = await self.query_bus.execute(
            SyncAndGetRequestQuery(request_id=handle.request_id, skip_cache=True)
        )
        self._last_dto[handle.request_id] = dto
        await self._drain_release()
        if check:
            self._check(handle, phase="sync")
        return dto

    async def crash_create(self, count: int) -> Handle:
        """Persist a request the way create does, then stop before dispatching it."""
        command = CreateRequestCommand(template_id=self.cell.template_id, requested_count=count)
        handler = self.container.get(get_command_handler_for_type(CreateRequestCommand))
        template = await handler._load_template(command.template_id)
        selection = await handler._provider_validation_service.select_and_validate_provider(
            template
        )
        request = handler._request_creation_service.create_machine_request(
            command, template, selection
        )
        await handler._persist_and_publish(request)
        handle = Handle(str(request.request_id), count)
        self.handles[handle.request_id] = handle
        self.scenario.request_ids.add(handle.request_id)
        return handle

    async def cancel(self, handle: Handle, reason: str = "cancelled by test") -> Any:
        """Cancel through the orchestrator, which aborts provisioning then flips the status."""
        from orb.application.services.orchestration.cancel_request import (
            CancelRequestOrchestrator,
        )
        from orb.application.services.orchestration.dtos import CancelRequestInput

        self.scenario.cancelled_requests.add(handle.request_id)
        orchestrator = self.container.get(CancelRequestOrchestrator)
        try:
            output = await orchestrator.execute(
                CancelRequestInput(request_id=handle.request_id, reason=reason)
            )
        finally:
            self._check(handle, phase="cancel")
        return output

    async def return_(self, machine_ids: list[str], *, track: bool = True) -> list[str]:
        if track:
            self.scenario.return_ids |= set(machine_ids)
        command = CreateReturnRequestCommand(machine_ids=machine_ids)
        await self.command_bus.execute(command)
        created = list(command.created_request_ids or [])
        note = getattr(self.cloud, "note_return_requests", None)
        if note is not None:
            note(created)
        for handle in self.handles.values():
            self._check(handle, phase="return")
        return created

    async def sync_return(self, return_request_id: str) -> Any:
        return await self.query_bus.execute(
            SyncAndGetRequestQuery(request_id=return_request_id, skip_cache=True)
        )

    # ---- release drain ------------------------------------------------------------

    async def _drain_release(self) -> None:
        """Wait for the deadline release resolved from this container, if present."""
        try:
            from orb.application.services.deadline_release_service import (  # type: ignore
                DeadlineReleaseService,
            )
        except ImportError:
            return
        service = self.container.get_optional(DeadlineReleaseService)
        if service is not None:
            await service.drain(30.0)

    # ---- observation --------------------------------------------------------------

    def view(self, handle: Handle) -> RequestView:
        with self.uow_factory.create_unit_of_work() as uow:
            request = uow.requests.find_by_request_id(handle.request_id)
            assert request is not None, f"request {handle.request_id} not stored"
            rows = {
                str(m.machine_id.value): m
                for m in uow.machines.find_by_request_id(handle.request_id)
            }
        dto = self._last_dto.get(handle.request_id)
        hf = None
        if dto is not None and dto.status == _status(request.status).value:
            hf = self.scheduler.format_request_status_response([dto])["requests"][0]
        return RequestView(
            request_id=handle.request_id,
            status=_status(request.status),
            request_type=request.request_type,
            machine_ids=[str(i) for i in request.machine_ids],
            metadata=dict(request.metadata or {}),
            rows=rows,
            dto=dto,
            hf_entry=hf,
        )

    def _check(self, handle: Handle, *, phase: str) -> None:
        self.scenario.requested = handle.requested
        self.scenario.dry_run = handle.dry_run
        oracle.assert_consistent(self.scenario, self.cloud, self.view(handle), phase=phase)

    def status(self, handle: Handle) -> RequestStatus:
        return self.view(handle).status


def register_clock(container: Any, clock: Clock) -> None:
    """Swap in ``clock`` and repoint singletons that already captured the real one."""
    from orb.application.services.deadline_release_service import DeadlineReleaseService
    from orb.application.services.machine_sync_service import MachineSyncService
    from orb.application.services.orchestration.acquire_machines import (
        AcquireMachinesOrchestrator,
    )
    from orb.application.services.orchestration.get_request_status import (
        GetRequestStatusOrchestrator,
    )
    from orb.application.services.orchestration.return_machines import (
        ReturnMachinesOrchestrator,
    )
    from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine

    container.register_instance(Clock, clock)
    container.get(FulfilmentStateMachine)._clock = clock
    container.get(MachineSyncService)._clock = clock
    release = container.get_optional(DeadlineReleaseService)
    if release is not None:
        release._clock = clock.now
        release._monotonic = clock.monotonic
    for orchestrator in (
        AcquireMachinesOrchestrator,
        GetRequestStatusOrchestrator,
        ReturnMachinesOrchestrator,
    ):
        container.get(orchestrator)._clock = clock
