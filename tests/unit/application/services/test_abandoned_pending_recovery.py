"""Startup recovery and read-path sweeps fail abandoned PENDING requests, and only those."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
)
from orb.application.services.request_status_service import RequestStatusService
from orb.domain.request.aggregate import Request
from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType


class _Repo:
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


class _Recorder:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, event: Any) -> None:
        self.events.append(event)


def _uow_factory(repo: _Repo) -> MagicMock:
    uow = MagicMock()
    uow.requests = repo
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    factory = MagicMock()
    factory.create_unit_of_work.return_value = uow
    return factory


def _pending(
    age_seconds: float, *, fulfillment_config: Any = None, resource_ids: list[str] | None = None
) -> Request:
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl",
        machine_count=2,
        provider_type="aws",
        provider_name="aws-default",
    )
    update: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    }
    if fulfillment_config is not None:
        update["metadata"] = {**request.metadata, "fulfillment_config": fulfillment_config}
    if resource_ids:
        update["resource_ids"] = resource_ids
    return request.model_copy(update=update)


def _state_machine() -> FulfilmentStateMachine:
    return FulfilmentStateMachine(grace_period_seconds=300)


def _recovery(repo: _Repo, recorder: _Recorder) -> ProvisioningOrchestrationService:
    config_port = MagicMock()
    config_port.get_request_config.return_value = {}
    return ProvisioningOrchestrationService(
        uow_factory=_uow_factory(repo),
        scheduler=MagicMock(),
        logger=MagicMock(),
        provider_selection_port=MagicMock(),
        provider_config_port=MagicMock(),
        config_port=config_port,
        circuit_breaker_factory=MagicMock(),
        state_machine=_state_machine(),
        event_publisher=recorder,
    )


def test_startup_scan_fails_an_old_pending_request_and_publishes_the_transition():
    repo = _Repo(_pending(3600))
    recorder = _Recorder()

    assert _recovery(repo, recorder).recover_stuck_acquiring_requests() == 1

    assert repo.stored.status == RequestStatus.FAILED
    assert repo.stored.status_message == "Request abandoned before provisioning started"
    changes = [e for e in recorder.events if e.event_type == "RequestStatusChangedEvent"]
    assert len(changes) == 1
    assert (changes[0].old_status, changes[0].new_status) == ("pending", "failed")


def test_startup_scan_leaves_a_pending_request_that_may_still_be_created():
    # Past the 300 s grace period, inside the 900 s create bound.
    repo = _Repo(_pending(400))
    recorder = _Recorder()

    assert _recovery(repo, recorder).recover_stuck_acquiring_requests() == 0

    assert repo.saves == 0
    assert repo.stored.status == RequestStatus.PENDING
    assert recorder.events == []


def test_startup_scan_leaves_a_pending_request_that_has_resource_ids():
    repo = _Repo(_pending(7200, resource_ids=["fleet-1"]))

    assert _recovery(repo, _Recorder()).recover_stuck_acquiring_requests() == 0

    assert repo.saves == 0


def test_startup_scan_does_not_force_fail_a_pending_request_with_a_long_override():
    # Older than the one-hour age fallback used for other active rows, but inside
    # its own bound of 4000 + 300 + 300 seconds.
    repo = _Repo(_pending(4000, fulfillment_config={"timeout_seconds": 4000}))
    recorder = _Recorder()

    assert _recovery(repo, recorder).recover_stuck_acquiring_requests() == 0

    assert repo.saves == 0
    assert repo.stored.status == RequestStatus.PENDING
    assert recorder.events == []


def test_startup_scan_fails_a_pending_request_past_its_own_override_bound():
    repo = _Repo(_pending(5000, fulfillment_config={"timeout_seconds": 4000}))

    assert _recovery(repo, _Recorder()).recover_stuck_acquiring_requests() == 1

    assert repo.stored.status == RequestStatus.FAILED


def test_read_path_sweep_fails_an_abandoned_pending_request():
    repo = _Repo(_pending(3600))
    recorder = _Recorder()
    service = RequestStatusService(
        _uow_factory(repo), MagicMock(), state_machine=_state_machine(), event_publisher=recorder
    )

    swept = service.sweep_expired_deadline(repo.stored)

    assert swept.status == RequestStatus.FAILED
    assert repo.stored.status == RequestStatus.FAILED
    assert len([e for e in recorder.events if e.event_type == "RequestStatusChangedEvent"]) == 1


def test_read_path_sweep_leaves_a_young_pending_request():
    repo = _Repo(_pending(100))
    service = RequestStatusService(_uow_factory(repo), MagicMock(), state_machine=_state_machine())

    service.sweep_expired_deadline(repo.stored)

    assert repo.saves == 0
