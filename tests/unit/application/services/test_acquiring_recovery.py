"""Unit tests for ProvisioningOrchestrationService.recover_stuck_acquiring_requests.

Covers:
- Expired ACQUIRING request → transitioned to FAILED.
- Non-expired ACQUIRING request → left untouched.
- No ACQUIRING rows → returns 0 (no-op).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
)
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(uow_factory: MagicMock) -> ProvisioningOrchestrationService:
    """Return a ProvisioningOrchestrationService with a pre-wired UoW factory."""
    from orb.domain.base import UnitOfWorkFactory

    container = MagicMock()

    def _container_get(cls):
        if cls is UnitOfWorkFactory:
            return uow_factory
        return MagicMock()

    container.get.side_effect = _container_get

    logger = MagicMock()
    config_port = MagicMock()
    config_port.get_request_config.return_value = {}

    return ProvisioningOrchestrationService(
        container=container,
        logger=logger,
        provider_selection_port=MagicMock(),
        provider_config_port=MagicMock(),
        config_port=config_port,
        circuit_breaker_factory=MagicMock(),
    )


def _make_acquiring_request(
    created_at: datetime,
    resource_ids: list[str] | None = None,
) -> Request:
    """Return a real Request domain object in ACQUIRING status."""
    req = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl-recovery-test",
        machine_count=1,
        provider_type="k8s",
        provider_name="k8s-cluster-1",
    )
    # Force the ACQUIRING status directly (bypassing normal transitions).
    req = req.update_status(RequestStatus.ACQUIRING, "provider resources created", force=True)
    # Override created_at to control age.
    req = req.model_copy(update={"created_at": created_at})
    if resource_ids:
        for rid in resource_ids:
            req = req.add_resource_id(rid)
    return req


def _make_uow_factory(requests: list[Request]) -> tuple[MagicMock, MagicMock]:
    """Return (uow_factory_mock, saved_requests_list) pair.

    The ``uow.requests.find_by_status`` call is backed by ``requests``.
    Each ``uow.requests.save`` call appends the argument to a list so tests
    can assert on what was persisted.
    """
    saved: list[Request] = []

    repo_mock = MagicMock()

    # The recovery scan queries ACQUIRING, IN_PROGRESS and PARTIAL_PENDING
    # separately. Return each supplied request only for the status it actually
    # holds so a single ACQUIRING row is not counted once per queried status.
    def _find_by_status(status: RequestStatus) -> list[Request]:
        return [r for r in requests if r.status == status]

    repo_mock.find_by_status.side_effect = _find_by_status

    def _save(req: Request) -> None:
        saved.append(req)

    repo_mock.save.side_effect = _save

    uow_mock = MagicMock()
    uow_mock.requests = repo_mock
    uow_mock.__enter__ = MagicMock(return_value=uow_mock)
    uow_mock.__exit__ = MagicMock(return_value=False)

    uow_factory = MagicMock()
    uow_factory.create_unit_of_work.return_value = uow_mock

    return uow_factory, saved


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRecoverStuckAcquiringRequests:
    """recover_stuck_acquiring_requests behaviour."""

    def test_no_acquiring_rows_is_noop(self):
        """When the repo has no ACQUIRING rows the method returns 0."""
        uow_factory, saved = _make_uow_factory([])
        svc = _make_service(uow_factory)

        result = svc.recover_stuck_acquiring_requests(timeout_seconds=3600)

        assert result == 0
        assert saved == []

    def test_expired_acquiring_transitioned_to_failed(self):
        """An ACQUIRING request older than timeout_seconds is transitioned to FAILED."""
        now = datetime.now(timezone.utc)
        old_ts = now - timedelta(seconds=7200)  # 2 hours ago; exceeds 3600s default

        req = _make_acquiring_request(created_at=old_ts, resource_ids=["pod-abc"])
        uow_factory, saved = _make_uow_factory([req])
        svc = _make_service(uow_factory)

        result = svc.recover_stuck_acquiring_requests(timeout_seconds=3600)

        assert result == 1
        assert len(saved) == 1
        failed_req: Request = saved[0]
        assert failed_req.status == RequestStatus.FAILED
        assert "timeout exceeded" in (failed_req.status_message or "")
        # Resource IDs are preserved for operator visibility.
        assert "pod-abc" in failed_req.resource_ids

    def test_non_expired_acquiring_left_alone(self):
        """An ACQUIRING request younger than timeout_seconds is not touched."""
        now = datetime.now(timezone.utc)
        recent_ts = now - timedelta(seconds=300)  # 5 minutes ago; well within 3600s

        req = _make_acquiring_request(created_at=recent_ts)
        uow_factory, saved = _make_uow_factory([req])
        svc = _make_service(uow_factory)

        result = svc.recover_stuck_acquiring_requests(timeout_seconds=3600)

        assert result == 0
        assert saved == []

    def test_mixed_expired_and_fresh_only_fails_expired(self):
        """Only requests that exceed the timeout threshold are failed."""
        now = datetime.now(timezone.utc)
        old_ts = now - timedelta(seconds=4000)
        fresh_ts = now - timedelta(seconds=100)

        old_req = _make_acquiring_request(created_at=old_ts, resource_ids=["pod-old"])
        fresh_req = _make_acquiring_request(created_at=fresh_ts, resource_ids=["pod-fresh"])
        uow_factory, saved = _make_uow_factory([old_req, fresh_req])
        svc = _make_service(uow_factory)

        result = svc.recover_stuck_acquiring_requests(timeout_seconds=3600)

        assert result == 1
        assert len(saved) == 1
        assert saved[0].status == RequestStatus.FAILED
        assert "pod-old" in saved[0].resource_ids


class TestRecoverSweepsAllActiveStatuses:
    """The recovery sweep must cover IN_PROGRESS and PARTIAL_PENDING (not just
    ACQUIRING) so a client that stops polling cannot leave a request active
    forever. Rows are judged expired by deadline_at when present."""

    def _make_active_request(
        self,
        status: RequestStatus,
        deadline_at: datetime,
        machine_ids: list[str] | None = None,
    ) -> Request:
        req = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="tpl-active",
            machine_count=3,
            provider_type="aws",
            provider_name="aws-1",
        )
        updates = {
            "status": status,
            "deadline_at": deadline_at,
            "started_at": deadline_at - timedelta(seconds=3600),
        }
        if machine_ids:
            updates["machine_ids"] = machine_ids
        return req.model_copy(update=updates)

    def test_expired_in_progress_swept_to_timeout(self):
        now = datetime.now(timezone.utc)
        req = self._make_active_request(
            RequestStatus.IN_PROGRESS, deadline_at=now - timedelta(seconds=10)
        )
        uow_factory, saved = _make_uow_factory([req])
        svc = _make_service(uow_factory)

        result = svc.recover_stuck_acquiring_requests(timeout_seconds=3600)

        assert result == 1
        assert len(saved) == 1
        # No capacity → TIMEOUT via the deadline sweep.
        assert saved[0].status == RequestStatus.TIMEOUT

    def test_expired_partial_pending_swept_to_partial(self):
        now = datetime.now(timezone.utc)
        req = self._make_active_request(
            RequestStatus.PARTIAL_PENDING,
            deadline_at=now - timedelta(seconds=10),
            machine_ids=["i-1"],  # 1/3 capacity → terminal PARTIAL
        )
        uow_factory, saved = _make_uow_factory([req])
        svc = _make_service(uow_factory)

        result = svc.recover_stuck_acquiring_requests(timeout_seconds=3600)

        assert result == 1
        assert saved[0].status == RequestStatus.PARTIAL

    def test_in_progress_within_deadline_left_alone(self):
        now = datetime.now(timezone.utc)
        req = self._make_active_request(
            RequestStatus.IN_PROGRESS, deadline_at=now + timedelta(seconds=600)
        )
        uow_factory, saved = _make_uow_factory([req])
        svc = _make_service(uow_factory)

        result = svc.recover_stuck_acquiring_requests(timeout_seconds=3600)

        assert result == 0
        assert saved == []
