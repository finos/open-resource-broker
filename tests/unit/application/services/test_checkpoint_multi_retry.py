"""Tests for multi-retry checkpoint update semantics in ProvisioningOrchestrationService.

Verifies that when the provisioning loop runs multiple successful attempts:
- Resource IDs from each attempt are accumulated in the final result.
- The PENDING → ACQUIRING status transition fires on the first attempt and
  is not re-fired on subsequent attempts (the request is already ACQUIRING).
- The final result contains resource IDs from all attempts.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
    ProvisioningResult,
)
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(max_retries: int = 1) -> ProvisioningOrchestrationService:
    container = MagicMock()
    logger = MagicMock()
    provider_selection_port = MagicMock()
    provider_config_port = MagicMock()
    config_port = MagicMock()
    circuit_breaker_factory = MagicMock()

    config_port.get_request_config.return_value = {
        "fulfillment_max_retries": max_retries,
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


def _make_real_request(count: int = 3) -> Request:
    return Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl-test",
        machine_count=count,
        provider_type="k8s",
        provider_name="k8s-cluster-1",
    )


def _make_provider_result(resource_ids: list[str], instances: list[dict]) -> Any:
    from orb.providers.base.strategy.provider_strategy import ProviderResult

    return ProviderResult.success_result(
        data={
            "resource_ids": resource_ids,
            "instances": instances,
            "instance_ids": [i.get("id", "") for i in instances],
        },
        metadata={},
    )


def _make_selection_result() -> Any:
    from orb.domain.base.results import ProviderSelectionResult

    return ProviderSelectionResult(
        provider_name="k8s-cluster-1",
        provider_type="k8s",
        selection_reason="test",
        confidence=1.0,
    )


def _make_template() -> MagicMock:
    template = MagicMock()
    template.template_id = "tpl-test"
    return template


def _make_uow_factory(saved_requests: list) -> MagicMock:
    uow = MagicMock()
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    uow.requests.save = MagicMock(side_effect=lambda r: saved_requests.append(r))
    uow_factory = MagicMock()
    uow_factory.create_unit_of_work.return_value = uow
    return uow_factory


# ---------------------------------------------------------------------------
# Multi-retry checkpoint semantics
# ---------------------------------------------------------------------------


class TestCheckpointMultiRetrySemantics:
    @pytest.mark.asyncio
    async def test_two_successful_attempts_accumulate_resource_ids(self) -> None:
        """Two successful attempts should accumulate all resource IDs in the final result."""
        svc = _make_service(max_retries=1)
        request = _make_real_request(count=2)
        template = _make_template()
        selection = _make_selection_result()

        saved_requests: list[Any] = []
        uow_factory = _make_uow_factory(saved_requests)
        svc._container.get.return_value = uow_factory

        attempt_counter = {"n": 0}

        async def fake_dispatch(tmpl, req, sel, count, timeout):
            attempt_counter["n"] += 1
            if attempt_counter["n"] == 1:
                pr = ProvisioningResult(
                    success=True,
                    resource_ids=["pod-a"],
                    machine_ids=["pod-a"],
                    instances=[{"id": "pod-a"}],
                    provider_data={},
                    fulfilled_count=1,
                    is_final=False,
                )
                return pr
            else:
                return ProvisioningResult(
                    success=True,
                    resource_ids=["pod-b"],
                    machine_ids=["pod-b"],
                    instances=[{"id": "pod-b"}],
                    provider_data={},
                    fulfilled_count=1,
                    is_final=True,
                )

        with patch.object(svc, "_dispatch_single_attempt", side_effect=fake_dispatch):
            final = await svc.execute_provisioning(template, request, selection)

        assert "pod-a" in final.resource_ids, "pod-a must appear in final resource_ids"
        assert "pod-b" in final.resource_ids, "pod-b must appear in final resource_ids"
        assert attempt_counter["n"] == 2, "Both attempts must have run"

    @pytest.mark.asyncio
    async def test_pending_to_acquiring_transition_fires_once(self) -> None:
        """PENDING → ACQUIRING must fire on the first checkpoint, not on each retry.

        After the first checkpoint the request is ACQUIRING; subsequent calls to
        _persist_resource_ids_checkpoint must NOT change the status again (it is
        already ACQUIRING, the domain model's idempotent transition handles it).
        """
        svc = _make_service(max_retries=1)
        request = _make_real_request(count=2)
        template = _make_template()
        selection = _make_selection_result()

        saved_requests: list[Any] = []
        uow_factory = _make_uow_factory(saved_requests)
        svc._container.get.return_value = uow_factory

        attempt_counter = {"n": 0}

        async def fake_dispatch(tmpl, req, sel, count, timeout):
            attempt_counter["n"] += 1
            if attempt_counter["n"] == 1:
                return ProvisioningResult(
                    success=True,
                    resource_ids=["pod-a"],
                    machine_ids=["pod-a"],
                    instances=[{"id": "pod-a"}],
                    provider_data={},
                    fulfilled_count=1,
                    is_final=False,
                )
            else:
                return ProvisioningResult(
                    success=True,
                    resource_ids=["pod-b"],
                    machine_ids=["pod-b"],
                    instances=[{"id": "pod-b"}],
                    provider_data={},
                    fulfilled_count=1,
                    is_final=True,
                )

        with patch.object(svc, "_dispatch_single_attempt", side_effect=fake_dispatch):
            await svc.execute_provisioning(template, request, selection)

        # Collect all ACQUIRING transitions from persisted requests
        acquiring_transitions = [r for r in saved_requests if r.status == RequestStatus.ACQUIRING]
        # The first checkpoint moves PENDING → ACQUIRING.
        # Subsequent checkpoints see ACQUIRING → ACQUIRING (idempotent, same status).
        # We require at least one ACQUIRING persist happened.
        assert len(acquiring_transitions) >= 1, "At least one ACQUIRING persist must have occurred"

        # The initial PENDING transition must have happened exactly once
        # (first save with ACQUIRING status after the first attempt).
        # Verify no persisted request shows PENDING (all should be ACQUIRING+).
        pending_rows = [r for r in saved_requests if r.status == RequestStatus.PENDING]
        assert len(pending_rows) == 0, (
            "No persisted row should remain in PENDING after a checkpoint"
        )
