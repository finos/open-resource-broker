"""The status poll decides the deadline outcome from the provider read and starts the release."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from orb.application.services.machine_sync_service import FailedProviderFetch
from orb.application.services.request_status_service import RequestStatusService
from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType

STAMP = "deadline_release"


class _Repo:
    def __init__(self, request: Request, order: Optional[list[str]] = None) -> None:
        self.stored = request
        self.conflicts = 0
        self.order: list[str] = order if order is not None else []

    def get_by_id(self, _request_id: Any) -> Request:
        return self.stored

    def save(self, request: Request) -> list[Any]:
        if self.conflicts:
            self.conflicts -= 1
            self.order.append("conflict")
            raise ConcurrencyError("lost")
        self.order.append("save")
        events = request.get_domain_events()
        request.clear_domain_events()
        self.stored = request
        return events


def _expired(machine_ids: Optional[list[str]] = None, requested: int = 3) -> Request:
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl",
        machine_count=requested,
        provider_type="aws",
        provider_name="aws-default",
    )
    return request.model_copy(
        update={
            "status": RequestStatus.ACQUIRING,
            "resource_ids": ["fleet-1"],
            "machine_ids": ["i-1", "i-2", "i-3"] if machine_ids is None else machine_ids,
            "deadline_at": datetime.now(timezone.utc) - timedelta(seconds=30),
        }
    )


def _m(mid: str, status: MachineStatus) -> Any:
    return SimpleNamespace(machine_id=SimpleNamespace(value=mid), status=status)


def _service(repo: _Repo, order: list[str], release: Any = None):
    uow = MagicMock()
    uow.requests = repo
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    factory = MagicMock()
    factory.create_unit_of_work.return_value = uow
    publisher = MagicMock()
    publisher.publish.side_effect = lambda event: order.append(f"publish:{event.event_type}")
    service = RequestStatusService(
        factory,
        MagicMock(),
        state_machine=FulfilmentStateMachine(grace_period_seconds=300),
        event_publisher=publisher,
        deadline_release=release,
    )
    return service


def _release(order: list[str]) -> MagicMock:
    from orb.application.services.deadline_release_service import DeadlineReleaseService

    release = MagicMock()
    release.enqueue_if_owed.side_effect = lambda r: (
        order.append("enqueue") if DeadlineReleaseService.is_owed(r) else None
    )
    return release


def _placeholder_verdict() -> ProviderFulfilment:
    # What several providers report while a lookup is still failing.
    return ProviderFulfilment(state="in_progress", message="not yet", running_count=0)


@pytest.mark.asyncio
async def test_a_placeholder_zero_with_no_machines_read_is_not_a_timeout():
    repo = _Repo(_expired())
    service = _service(repo, [])

    result = await service.update_request_status(
        repo.stored,
        "in_progress",
        "",
        {"provider_fulfilment": _placeholder_verdict()},
        [],
    )

    assert result.status == RequestStatus.COMPLETED
    assert STAMP not in result.metadata


@pytest.mark.asyncio
async def test_machines_read_but_none_running_after_the_deadline_times_out_and_is_marked():
    repo = _Repo(_expired())
    service = _service(repo, [])
    machines = [_m(i, MachineStatus.PENDING) for i in ("i-1", "i-2", "i-3")]

    result = await service.update_request_status(
        repo.stored,
        "in_progress",
        "",
        {"provider_fulfilment": _placeholder_verdict()},
        machines,
    )

    assert result.status == RequestStatus.TIMEOUT
    assert repo.stored.metadata[STAMP] == "pending"


@pytest.mark.asyncio
async def test_a_failed_fetch_counts_as_unknown():
    repo = _Repo(_expired())
    service = _service(repo, [])
    stored = FailedProviderFetch([_m("i-1", MachineStatus.PENDING)])

    result = await service.update_request_status(
        repo.stored,
        "in_progress",
        "",
        {"provider_fulfilment": _placeholder_verdict()},
        stored,
    )

    assert result.status == RequestStatus.COMPLETED


@pytest.mark.asyncio
async def test_machines_outside_the_request_are_not_counted():
    repo = _Repo(_expired())
    service = _service(repo, [])
    machines = [
        _m("i-1", MachineStatus.RUNNING),
        _m("replacement-1", MachineStatus.RUNNING),
        _m("replacement-2", MachineStatus.RUNNING),
        _m("replacement-3", MachineStatus.RUNNING),
    ]

    result = await service.update_request_status(
        repo.stored,
        "in_progress",
        "",
        {"provider_fulfilment": _placeholder_verdict()},
        machines,
    )

    assert result.status == RequestStatus.PARTIAL


@pytest.mark.asyncio
async def test_a_positive_verdict_count_is_used_when_the_read_returned_none_of_our_machines():
    repo = _Repo(_expired())
    service = _service(repo, [])
    verdict = ProviderFulfilment(state="in_progress", message="", running_count=1)

    result = await service.update_request_status(
        repo.stored, "in_progress", "", {"provider_fulfilment": verdict}, []
    )

    assert result.status == RequestStatus.PARTIAL


@pytest.mark.asyncio
async def test_the_release_starts_once_after_the_transition_is_published():
    order: list[str] = []
    repo = _Repo(_expired(), order)
    service = _service(repo, order, _release(order))
    machines = [_m(i, MachineStatus.PENDING) for i in ("i-1", "i-2", "i-3")]

    await service.update_request_status(
        repo.stored,
        "in_progress",
        "",
        {"provider_fulfilment": _placeholder_verdict()},
        machines,
    )

    assert order == ["save", "publish:RequestStatusChangedEvent", "enqueue"]


@pytest.mark.asyncio
async def test_the_release_is_not_started_inside_a_retried_write():
    order: list[str] = []
    repo = _Repo(_expired(), order)
    repo.conflicts = 1
    service = _service(repo, order, _release(order))
    machines = [_m(i, MachineStatus.PENDING) for i in ("i-1", "i-2", "i-3")]

    await service.update_request_status(
        repo.stored,
        "in_progress",
        "",
        {"provider_fulfilment": _placeholder_verdict()},
        machines,
    )

    assert order == ["conflict", "save", "publish:RequestStatusChangedEvent", "enqueue"]


@pytest.mark.asyncio
async def test_a_completed_outcome_starts_no_release():
    order: list[str] = []
    repo = _Repo(_expired(), order)
    service = _service(repo, order, _release(order))
    machines = [_m(i, MachineStatus.RUNNING) for i in ("i-1", "i-2", "i-3")]

    result = await service.update_request_status(
        repo.stored,
        "in_progress",
        "",
        {"provider_fulfilment": _placeholder_verdict()},
        machines,
    )

    assert result.status == RequestStatus.COMPLETED
    assert "enqueue" not in order


@pytest.mark.asyncio
async def test_the_poll_returns_while_a_slow_release_is_still_running():
    from orb.application.services.deadline_release_service import DeadlineReleaseService

    repo = _Repo(_expired())
    order: list[str] = []
    finish = asyncio.Event()
    provider_machines = [_m(i, MachineStatus.PENDING) for i in ("i-1", "i-2", "i-3")]
    sync = MagicMock()

    async def fetch(request: Any, db: Any) -> Any:
        return provider_machines, {}

    sync.fetch_provider_machines = fetch
    bus = MagicMock()

    async def slow(command: Any) -> None:
        await finish.wait()

    bus.execute = slow
    uow = MagicMock()
    uow.requests = repo
    uow.machines.find_by_request_id.return_value = []
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    factory = MagicMock()
    factory.create_unit_of_work.return_value = uow
    release = DeadlineReleaseService(factory, sync, bus, MagicMock())
    service = _service(repo, order, release)

    result = await asyncio.wait_for(
        service.update_request_status(
            repo.stored,
            "in_progress",
            "",
            {"provider_fulfilment": _placeholder_verdict()},
            provider_machines,
        ),
        1,
    )
    finish.set()
    await release.drain(2)

    assert result.status == RequestStatus.TIMEOUT
