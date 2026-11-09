"""Startup recovery: which expired rows the state machine decides and which are force-failed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import MagicMock

from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
)
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType

STAMP = "deadline_release"


class _Repo:
    def __init__(self, request: Request) -> None:
        self.stored = request
        self.saves: list[Request] = []

    def find_by_status(self, status: RequestStatus) -> list[Request]:
        return [self.stored] if self.stored.status == status else []

    def save(self, request: Request) -> list[Any]:
        self.saves.append(request)
        self.stored = request
        return []


def _service(repo: _Repo) -> ProvisioningOrchestrationService:
    uow = MagicMock()
    uow.requests = repo
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    factory = MagicMock()
    factory.create_unit_of_work.return_value = uow
    config = MagicMock()
    config.get_request_config.return_value = {}
    return ProvisioningOrchestrationService(
        uow_factory=factory,
        scheduler=MagicMock(),
        logger=MagicMock(),
        provider_selection_port=MagicMock(),
        provider_config_port=MagicMock(),
        config_port=config,
        circuit_breaker_factory=MagicMock(),
    )


def _row(
    *,
    deadline_ago: Optional[float],
    machine_ids: Optional[list[str]] = None,
    snapshot: Optional[dict[str, Any]] = None,
    created_ago: float = 7200,
) -> Request:
    now = datetime.now(timezone.utc)
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl",
        machine_count=3,
        provider_type="aws",
        provider_name="aws-default",
    )
    metadata = dict(request.metadata)
    if snapshot is not None:
        metadata["last_fulfilment"] = snapshot
    return request.model_copy(
        update={
            "status": RequestStatus.ACQUIRING,
            "resource_ids": ["fleet-1"],
            "machine_ids": machine_ids or [],
            "created_at": now - timedelta(seconds=created_ago),
            "deadline_at": None if deadline_ago is None else now - timedelta(seconds=deadline_ago),
            "metadata": metadata,
        }
    )


def test_expired_row_with_ids_waits_for_a_provider_read_instead_of_being_force_failed():
    repo = _Repo(_row(deadline_ago=60, machine_ids=["i-1", "i-2", "i-3"]))

    swept = _service(repo).recover_stuck_acquiring_requests()

    assert swept == 0
    assert repo.saves == []


def test_row_past_the_backstop_is_finalised_from_stored_evidence_and_marked():
    repo = _Repo(
        _row(
            deadline_ago=7200,
            machine_ids=["i-1", "i-2", "i-3"],
            snapshot={"state": "in_progress", "running_count": 1},
        )
    )

    swept = _service(repo).recover_stuck_acquiring_requests()

    assert swept == 1
    assert repo.saves[0].status == RequestStatus.PARTIAL
    assert repo.saves[0].metadata[STAMP] == "pending"


def test_row_with_a_deadline_and_no_machine_ids_is_decided_by_the_state_machine():
    repo = _Repo(_row(deadline_ago=60))

    swept = _service(repo).recover_stuck_acquiring_requests()

    assert swept == 1
    assert repo.saves[0].status == RequestStatus.TIMEOUT
    assert STAMP not in repo.saves[0].metadata


def test_legacy_row_without_a_deadline_is_force_failed():
    repo = _Repo(_row(deadline_ago=None, machine_ids=["i-1"], created_ago=7200))

    swept = _service(repo).recover_stuck_acquiring_requests()

    assert swept == 1
    assert repo.saves[0].status == RequestStatus.FAILED


def test_pending_row_is_decided_by_the_state_machine_only():
    pending = _row(deadline_ago=None, created_ago=36000).model_copy(
        update={"status": RequestStatus.PENDING, "resource_ids": [], "machine_ids": []}
    )
    repo = _Repo(pending)

    swept = _service(repo).recover_stuck_acquiring_requests()

    assert swept == 1
    assert repo.saves[0].status == RequestStatus.FAILED
    assert repo.saves[0].status_message == "Request abandoned before provisioning started"
