"""Status transitions made by polling, the deadline sweep and startup recovery are published.

Each of these paths saves the request itself, so the events the save returns have
to be handed to the publisher by the service; otherwise subscribers (metrics, live
updates) never see the transition.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.application.events.base.event_handler import EventHandler
from orb.application.events.bus.event_bus import EventBus
from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
)
from orb.application.services.request_status_service import RequestStatusService
from orb.domain.request.aggregate import Request
from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType
from orb.infrastructure.events.publisher import ConfigurableEventPublisher


class _Repo:
    """In-memory request repository that, like the real one, returns saved events."""

    def __init__(self, request: Request) -> None:
        self.stored = request
        self.saves = 0

    def get_by_id(self, _request_id: Any) -> Request:
        return self.stored

    def find_by_status(self, status: RequestStatus) -> list[Request]:
        return [self.stored] if self.stored.status == status else []

    def save(self, request: Request) -> list[Any]:
        self.saves += 1
        events = request.get_domain_events()
        request.clear_domain_events()
        self.stored = request
        return events


def _uow_factory(repo: _Repo) -> MagicMock:
    uow = MagicMock()
    uow.requests = repo
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    factory = MagicMock()
    factory.create_unit_of_work.return_value = uow
    return factory


class _Recorder:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, event: Any) -> None:
        self.events.append(event)

    def status_changes(self) -> list[Any]:
        return [e for e in self.events if e.event_type == "RequestStatusChangedEvent"]


def _in_progress_request(*, deadline_in: timedelta | None = None, status=RequestStatus.IN_PROGRESS):
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl",
        machine_count=2,
        provider_type="aws",
        provider_name="aws-default",
    )
    request = request.update_status(status, "started", force=True)
    if deadline_in is not None:
        request = request.model_copy(
            update={"deadline_at": datetime.now(timezone.utc) + deadline_in}
        )
    request.clear_domain_events()
    return request


def _service(repo: _Repo, publisher: Any) -> RequestStatusService:
    return RequestStatusService(
        _uow_factory(repo),
        MagicMock(),
        state_machine=FulfilmentStateMachine(grace_period_seconds=3600),
        event_publisher=publisher,
    )


@pytest.mark.asyncio
async def test_polled_failure_publishes_one_status_change():
    repo = _Repo(_in_progress_request())
    recorder = _Recorder()

    await _service(repo, recorder).update_request_status(repo.stored, "failed", "boom")

    assert repo.stored.status == RequestStatus.FAILED
    changes = recorder.status_changes()
    assert len(changes) == 1
    assert (changes[0].old_status, changes[0].new_status) == ("in_progress", "failed")


@pytest.mark.asyncio
async def test_polled_completion_publishes_status_change_and_completion_once():
    repo = _Repo(_in_progress_request())
    recorder = _Recorder()

    await _service(repo, recorder).update_request_status(repo.stored, "complete", "done")

    assert [e.event_type for e in recorder.events].count("RequestStatusChangedEvent") == 1
    assert [e.event_type for e in recorder.events].count("RequestCompletedEvent") == 1


@pytest.mark.asyncio
async def test_unchanged_poll_publishes_nothing_more_on_repeat():
    repo = _Repo(_in_progress_request())
    recorder = _Recorder()
    service = _service(repo, recorder)

    await service.update_request_status(repo.stored, "failed", "boom")
    await service.update_request_status(repo.stored, "failed", "boom")

    assert len(recorder.status_changes()) == 1


def test_deadline_sweep_publishes_one_status_change():
    repo = _Repo(_in_progress_request(deadline_in=timedelta(seconds=-5)))
    recorder = _Recorder()

    swept = _service(repo, recorder).sweep_expired_deadline(repo.stored)

    assert swept.status == RequestStatus.TIMEOUT
    changes = recorder.status_changes()
    assert len(changes) == 1
    assert changes[0].new_status == "timeout"


def test_deadline_sweep_that_changes_nothing_publishes_nothing():
    repo = _Repo(_in_progress_request(deadline_in=timedelta(hours=1)))
    recorder = _Recorder()

    _service(repo, recorder).sweep_expired_deadline(repo.stored)

    assert recorder.events == []


def test_startup_scan_publishes_one_status_change_per_swept_request():
    repo = _Repo(
        _in_progress_request(deadline_in=timedelta(seconds=-5), status=RequestStatus.ACQUIRING)
    )
    recorder = _Recorder()
    config_port = MagicMock()
    config_port.get_request_config.return_value = {}
    service = ProvisioningOrchestrationService(
        uow_factory=_uow_factory(repo),
        scheduler=MagicMock(),
        logger=MagicMock(),
        provider_selection_port=MagicMock(),
        provider_config_port=MagicMock(),
        config_port=config_port,
        circuit_breaker_factory=MagicMock(),
        event_publisher=recorder,
    )

    assert service.recover_stuck_acquiring_requests() == 1

    changes = recorder.status_changes()
    assert len(changes) == 1
    assert changes[0].new_status == "timeout"


class _Collector(EventHandler):
    def __init__(self) -> None:
        super().__init__(None)
        self.received: list[Any] = []

    async def process_event(self, event: Any) -> None:
        self.received.append(event)


@pytest.mark.asyncio
async def test_sweep_run_in_a_worker_thread_reaches_the_bus_handler_once():
    repo = _Repo(_in_progress_request(deadline_in=timedelta(seconds=-5)))
    bus = EventBus()
    collector = _Collector()
    bus.register_handler("RequestStatusChangedEvent", collector)
    publisher = ConfigurableEventPublisher(mode="logging", event_bus=bus)
    publisher.bind_loop(asyncio.get_running_loop())

    swept = await asyncio.to_thread(_service(repo, publisher).sweep_expired_deadline, repo.stored)
    await publisher.drain()

    assert swept.status == RequestStatus.TIMEOUT
    assert len(collector.received) == 1
    assert collector.received[0].new_status == "timeout"
