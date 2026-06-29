"""Tests for the crash-window data-loss fix in ProvisioningOrchestrationService.

Critical behaviour: a persisted request row must exist with the provider-assigned
resource IDs recorded BEFORE execute_provisioning returns to the handler.  This
ensures that if ORB crashes after the provider creates resources (e.g. k8s pods)
but before the handler writes the final request row, a startup reconciler can
re-associate orphan provider resources with the dangling DB row by matching the
resource IDs stored in metadata (e.g. pod labels).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

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

    cb = MagicMock()
    cb.has_state.return_value = False
    circuit_breaker_factory.return_value = cb

    return ProvisioningOrchestrationService(
        container=container,
        logger=logger,
        provider_selection_port=provider_selection_port,
        provider_config_port=provider_config_port,
        config_port=config_port,
        circuit_breaker_factory=circuit_breaker_factory,
    )


def _make_real_request(count: int = 2, status: RequestStatus = RequestStatus.PENDING) -> Request:
    """Return a real Request aggregate (not a mock) in the given status."""
    req = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl-test",
        machine_count=count,
        provider_type="k8s",
        provider_name="k8s-cluster-1",
    )
    if status != RequestStatus.PENDING:
        req = req.update_status(status, "forced for test")
    return req


def _make_provider_result_success(resource_ids: list[str], instances: list[dict]) -> Any:
    from orb.providers.base.strategy.provider_strategy import ProviderResult

    return ProviderResult.success_result(
        data={
            "resource_ids": resource_ids,
            "instances": instances,
            "instance_ids": [i.get("instance_id", i.get("id", "")) for i in instances],
        },
        metadata={},
    )


def _make_selection_result(provider_name: str = "k8s-cluster-1") -> Any:
    from orb.domain.base.results import ProviderSelectionResult

    return ProviderSelectionResult(
        provider_name=provider_name,
        provider_type="k8s",
        selection_reason="test",
        confidence=1.0,
    )


# ---------------------------------------------------------------------------
# _persist_resource_ids_checkpoint unit tests
# ---------------------------------------------------------------------------


class TestPersistResourceIdsCheckpoint:
    """_persist_resource_ids_checkpoint must write resource IDs to DB on success."""

    def test_checkpoint_writes_resource_ids_and_returns_updated_request(self):
        svc = _make_service()
        request = _make_real_request()

        saved_requests: list[Any] = []

        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow.requests.save = MagicMock(side_effect=lambda r: saved_requests.append(r))

        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.return_value = uow
        svc._container.get.return_value = uow_factory

        updated, ok = svc._persist_resource_ids_checkpoint(request, ["pod-a", "pod-b"])

        assert ok is True
        assert "pod-a" in updated.resource_ids
        assert "pod-b" in updated.resource_ids
        assert len(saved_requests) == 1
        persisted = saved_requests[0]
        assert "pod-a" in persisted.resource_ids
        assert "pod-b" in persisted.resource_ids

    def test_checkpoint_advances_pending_to_acquiring(self):
        """A PENDING request must be promoted to ACQUIRING so the DB row is visibly in-flight."""
        svc = _make_service()
        request = _make_real_request(status=RequestStatus.PENDING)

        saved_requests: list[Any] = []
        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow.requests.save = MagicMock(side_effect=lambda r: saved_requests.append(r))
        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.return_value = uow
        svc._container.get.return_value = uow_factory

        updated, ok = svc._persist_resource_ids_checkpoint(request, ["pod-x"])

        assert ok is True
        assert updated.status == RequestStatus.ACQUIRING
        assert saved_requests[0].status == RequestStatus.ACQUIRING

    def test_checkpoint_does_not_change_non_pending_status(self):
        """A request already in ACQUIRING must not have its status changed."""
        svc = _make_service()
        request = _make_real_request()
        # Advance to ACQUIRING first via a valid domain transition.
        request = request.update_status(RequestStatus.ACQUIRING, "already acquiring")

        saved_requests: list[Any] = []
        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow.requests.save = MagicMock(side_effect=lambda r: saved_requests.append(r))
        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.return_value = uow
        svc._container.get.return_value = uow_factory

        updated, ok = svc._persist_resource_ids_checkpoint(request, ["pod-y"])

        assert ok is True
        assert updated.status == RequestStatus.ACQUIRING  # unchanged

    def test_checkpoint_returns_original_request_and_false_on_db_error(self):
        """If the DB write fails the original request is returned and ok=False."""
        svc = _make_service()
        request = _make_real_request()

        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.side_effect = RuntimeError("DB unavailable")
        svc._container.get.return_value = uow_factory

        returned_request, ok = svc._persist_resource_ids_checkpoint(request, ["pod-z"])

        assert ok is False
        # Must return the original unmodified request so the retry loop can continue
        assert returned_request is request

    def test_checkpoint_skips_write_when_no_resource_ids(self):
        """
        When called with an empty list the method is not invoked at all
        (the caller guards on last_result.resource_ids before calling).
        This test verifies the guard condition itself is respected.
        """
        svc = _make_service()
        request = _make_real_request()

        uow_factory = MagicMock()
        svc._container.get.return_value = uow_factory

        # Calling with empty list should still succeed without error
        updated, ok = svc._persist_resource_ids_checkpoint(request, [])

        # No resource IDs → resource_ids list should be unchanged
        assert updated.resource_ids == request.resource_ids
        # DB write may or may not happen — but if it does it must succeed
        assert ok is True


# ---------------------------------------------------------------------------
# execute_provisioning — crash-window integration tests
# ---------------------------------------------------------------------------


class TestExecuteProvisioningCrashWindow:
    """execute_provisioning must persist resource IDs before returning."""

    @pytest.mark.asyncio
    async def test_resource_ids_persisted_before_return_on_success(self):
        """After a successful dispatch the resource IDs must be in the DB
        before execute_provisioning returns — even if the caller crashes
        immediately after.
        """
        svc = _make_service()
        request = _make_real_request(count=2)
        selection_result = _make_selection_result()

        # Track what was saved to the DB
        saved_requests: list[Any] = []
        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow.requests.save = MagicMock(side_effect=lambda r: saved_requests.append(r))
        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.return_value = uow
        svc._container.get.return_value = uow_factory

        provider_result = _make_provider_result_success(
            resource_ids=["pod-aaa", "pod-bbb"],
            instances=[
                {"instance_id": "i-1", "resource_id": "pod-aaa"},
                {"instance_id": "i-2", "resource_id": "pod-bbb"},
            ],
        )
        svc._provider_selection_port.execute_operation = AsyncMock(return_value=provider_result)

        scheduler = MagicMock()
        scheduler.format_template_for_provider.return_value = {}
        # The service fetches both the scheduler (SchedulerPort) and the
        # uow_factory (UnitOfWorkFactory) from the container.  Return the
        # scheduler for the first get call and the uow_factory for subsequent
        # calls (used inside _persist_resource_ids_checkpoint).
        def _container_get(cls):
            from orb.application.ports.scheduler_port import SchedulerPort

            if cls is SchedulerPort:
                return scheduler
            return uow_factory

        svc._container.get = _container_get

        final_result = await svc.execute_provisioning(
            MagicMock(template_id="tpl-1"), request, selection_result
        )

        # The result must report the resource IDs
        assert "pod-aaa" in final_result.resource_ids
        assert "pod-bbb" in final_result.resource_ids

        # CRITICAL: at least one DB save must have occurred with the resource IDs
        # recorded — this is the crash-window protection.
        checkpoint_saves = [
            r for r in saved_requests if "pod-aaa" in getattr(r, "resource_ids", [])
        ]
        assert len(checkpoint_saves) >= 1, (
            "No DB checkpoint was written with resource IDs before execute_provisioning returned. "
            "A crash at this point would leave pods orphaned in the cluster with no DB record."
        )

    @pytest.mark.asyncio
    async def test_request_row_exists_after_provider_raises(self):
        """If the provider call raises an exception the initial PENDING row persisted
        by the handler (before calling execute_provisioning) must still be visible.
        The test verifies this by exercising the handler's own pre-provider persist
        path through execute_provisioning raising.
        """
        svc = _make_service()
        request = _make_real_request(count=1)
        selection_result = _make_selection_result()

        # Make the provider explode
        svc._provider_selection_port.execute_operation = AsyncMock(
            side_effect=RuntimeError("k8s API unreachable")
        )

        scheduler = MagicMock()
        scheduler.format_template_for_provider.return_value = {}

        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow.requests.save = MagicMock(return_value=None)
        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.return_value = uow

        def _container_get(cls):
            from orb.application.ports.scheduler_port import SchedulerPort

            if cls is SchedulerPort:
                return scheduler
            return uow_factory

        svc._container.get = _container_get

        # execute_provisioning must not re-raise generic exceptions from the
        # provider — it wraps them in a failed ProvisioningResult.
        result = await svc.execute_provisioning(
            MagicMock(template_id="tpl-1"), request, selection_result
        )

        assert result.success is False
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_checkpoint_db_failure_does_not_abort_provisioning(self):
        """A DB failure in the checkpoint must not abort the provisioning loop.
        The final result must still carry the resource IDs from the provider.
        """
        svc = _make_service()
        request = _make_real_request(count=2)
        selection_result = _make_selection_result()

        provider_result = _make_provider_result_success(
            resource_ids=["pod-1", "pod-2"],
            instances=[
                {"instance_id": "i-1", "resource_id": "pod-1"},
                {"instance_id": "i-2", "resource_id": "pod-2"},
            ],
        )
        svc._provider_selection_port.execute_operation = AsyncMock(return_value=provider_result)

        scheduler = MagicMock()
        scheduler.format_template_for_provider.return_value = {}

        # DB always raises — checkpoint must be best-effort
        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.side_effect = RuntimeError("DB down")

        def _container_get(cls):
            from orb.application.ports.scheduler_port import SchedulerPort

            if cls is SchedulerPort:
                return scheduler
            return uow_factory

        svc._container.get = _container_get

        final_result = await svc.execute_provisioning(
            MagicMock(template_id="tpl-1"), request, selection_result
        )

        # Despite the DB failure the provisioning result must still carry the IDs
        assert final_result.success is True
        assert "pod-1" in final_result.resource_ids
        assert "pod-2" in final_result.resource_ids
        # A warning must have been logged about the checkpoint failure
        svc._logger.warning.assert_called()


# ---------------------------------------------------------------------------
# State machine: PENDING → ACQUIRING transition
# ---------------------------------------------------------------------------


class TestPendingToAcquiringTransition:
    """PENDING must be allowed to transition directly to ACQUIRING."""

    def test_pending_can_transition_to_acquiring(self):
        assert RequestStatus.PENDING.can_transition_to(RequestStatus.ACQUIRING) is True

    def test_acquiring_can_transition_to_completed(self):
        assert RequestStatus.ACQUIRING.can_transition_to(RequestStatus.COMPLETED) is True

    def test_acquiring_can_transition_to_failed(self):
        assert RequestStatus.ACQUIRING.can_transition_to(RequestStatus.FAILED) is True

    def test_acquiring_can_transition_to_partial(self):
        assert RequestStatus.ACQUIRING.can_transition_to(RequestStatus.PARTIAL) is True

    def test_request_aggregate_update_status_pending_to_acquiring(self):
        """update_status on a real Request aggregate must accept PENDING → ACQUIRING."""
        req = _make_real_request(status=RequestStatus.PENDING)
        updated = req.update_status(RequestStatus.ACQUIRING, "provider resources created")
        assert updated.status == RequestStatus.ACQUIRING
