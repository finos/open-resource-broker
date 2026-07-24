"""Tests for the ACQUIRING supervisor (recover_stuck_acquiring_requests).

Verifies the startup-scan behaviour that transitions stale ACQUIRING rows to
FAILED while leaving non-expired rows untouched.
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


def _make_service() -> ProvisioningOrchestrationService:
    container = MagicMock()
    logger = MagicMock()
    provider_selection_port = MagicMock()
    provider_config_port = MagicMock()
    config_port = MagicMock()
    circuit_breaker_factory = MagicMock()

    config_port.get_request_config.return_value = {
        "fulfillment_max_retries": 0,
        "fulfillment_timeout_seconds": 300,
        "dispatch_timeout_seconds": 10.0,
        "fulfillment_batch_size": 1000,
    }

    return ProvisioningOrchestrationService(
        container=container,
        logger=logger,
        provider_selection_port=provider_selection_port,
        provider_config_port=provider_config_port,
        config_port=config_port,
        circuit_breaker_factory=circuit_breaker_factory,
    )


def _make_acquiring_request(
    created_at: datetime,
    resource_ids: list[str] | None = None,
) -> Request:
    """Create a Request in ACQUIRING status with a specified created_at."""
    req = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl-test",
        machine_count=1,
        provider_type="k8s",
        provider_name="k8s-cluster-1",
    )
    req = req.update_status(RequestStatus.ACQUIRING, "acquiring for test")
    if resource_ids:
        for rid in resource_ids:
            req = req.add_resource_id(rid)
    # Override created_at to simulate age
    req = req.model_copy(update={"created_at": created_at})
    return req


def _make_uow_factory(requests_by_status: dict[RequestStatus, list[Request]]) -> MagicMock:
    """Build a UoW factory that returns requests keyed by status."""
    saved_requests: list[Request] = []

    uow = MagicMock()
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)

    def _find_by_status(status: RequestStatus) -> list[Request]:
        return requests_by_status.get(status, [])

    uow.requests.find_by_status = MagicMock(side_effect=_find_by_status)
    uow.requests.save = MagicMock(side_effect=lambda r: saved_requests.append(r))

    uow_factory = MagicMock()
    uow_factory.create_unit_of_work.return_value = uow
    uow_factory._saved = saved_requests
    return uow_factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAcquiringSupervisor:
    def test_expired_acquiring_row_transitions_to_failed(self) -> None:
        """A request in ACQUIRING whose created_at is older than the timeout
        must be transitioned to FAILED."""
        svc = _make_service()

        now = datetime.now(timezone.utc)
        old_time = now - timedelta(seconds=7200)  # 2 hours ago
        expired_request = _make_acquiring_request(created_at=old_time, resource_ids=["pod-a"])

        saved_requests: list[Request] = []
        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow.requests.find_by_status = MagicMock(
            side_effect=lambda status: [r for r in [expired_request] if r.status == status]
        )
        uow.requests.save = MagicMock(side_effect=lambda r: saved_requests.append(r))

        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.return_value = uow
        svc._container.get.return_value = uow_factory

        result = svc.recover_stuck_acquiring_requests(timeout_seconds=3600)

        assert result == 1, "One expired ACQUIRING request must be transitioned"
        assert len(saved_requests) >= 1
        failed_row = saved_requests[-1]
        assert failed_row.status == RequestStatus.FAILED, (
            f"Expired request must be FAILED, got {failed_row.status}"
        )

    def test_non_expired_acquiring_row_left_alone(self) -> None:
        """A request in ACQUIRING that is within the timeout must NOT be touched."""
        svc = _make_service()

        now = datetime.now(timezone.utc)
        recent_time = now - timedelta(seconds=60)  # 1 minute ago, well within 1-hour timeout
        recent_request = _make_acquiring_request(created_at=recent_time, resource_ids=["pod-b"])

        saved_requests: list[Request] = []
        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow.requests.find_by_status = MagicMock(return_value=[recent_request])
        uow.requests.save = MagicMock(side_effect=lambda r: saved_requests.append(r))

        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.return_value = uow
        svc._container.get.return_value = uow_factory

        result = svc.recover_stuck_acquiring_requests(timeout_seconds=3600)

        assert result == 0, "No transitions should happen for a non-expired request"
        assert len(saved_requests) == 0, "Non-expired ACQUIRING request must not be saved"

    def test_no_acquiring_rows_is_noop(self) -> None:
        """When there are no ACQUIRING requests the scan must return 0 cleanly."""
        svc = _make_service()

        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow.requests.find_by_status = MagicMock(return_value=[])
        uow.requests.save = MagicMock()

        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.return_value = uow
        svc._container.get.return_value = uow_factory

        result = svc.recover_stuck_acquiring_requests(timeout_seconds=3600)

        assert result == 0, "No transitions when no ACQUIRING rows exist"
        uow.requests.save.assert_not_called()

    def test_resource_ids_preserved_on_transition(self) -> None:
        """When an ACQUIRING row is transitioned to FAILED its resource_ids must be
        preserved in the saved row so downstream cleanup can still find the resources."""
        svc = _make_service()

        now = datetime.now(timezone.utc)
        old_time = now - timedelta(seconds=7200)
        resource_ids = ["pod-x", "pod-y", "pod-z"]
        expired_request = _make_acquiring_request(created_at=old_time, resource_ids=resource_ids)

        saved_requests: list[Request] = []
        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow.requests.find_by_status = MagicMock(
            side_effect=lambda status: [r for r in [expired_request] if r.status == status]
        )
        uow.requests.save = MagicMock(side_effect=lambda r: saved_requests.append(r))

        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.return_value = uow
        svc._container.get.return_value = uow_factory

        svc.recover_stuck_acquiring_requests(timeout_seconds=3600)

        assert len(saved_requests) >= 1
        failed_row = saved_requests[-1]
        assert failed_row.status == RequestStatus.FAILED

        for rid in resource_ids:
            assert rid in failed_row.resource_ids, (
                f"resource_id {rid!r} must be preserved in the FAILED row"
            )
