"""Unit tests for phantom-termination gating in ``K8sHandlerRegistry.get_status``.

Verifies that IDs absent from ``request.provider_data["pod_names"]`` are
surfaced as ``status="unknown"`` rather than ``status="terminated"``, which
prevents ORB from writing TERMINATED rows for pods that were never
successfully submitted to the cluster.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.base.operation_outcome import Accepted, Completed
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_acquire_request(
    *,
    request_id: str | None = None,
    pod_names: list[str] | None = None,
) -> Request:
    """Return request with provider_data["pod_names"] set."""
    return Request(
        request_id=RequestId(value=request_id or f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="tpl-1",
        requested_count=len(pod_names or []),
        provider_data={
            "namespace": "orb-test",
            "pod_names": pod_names or [],
        },
    )


def _make_return_request(
    *,
    request_id: str | None = None,
    pod_names: list[str] | None = None,
) -> Request:
    return Request(
        request_id=RequestId(value=request_id or f"req-{uuid.uuid4()}"),
        request_type=RequestType.RETURN,
        provider_type="k8s",
        provider_api="Pod",
        template_id="tpl-1",
        requested_count=0,
        provider_data={
            "namespace": "orb-test",
            "pod_names": pod_names or [],
        },
    )


def _make_registry(*, check_result: CheckHostsStatusResult) -> K8sHandlerRegistry:
    """Build a registry whose Pod handler stub returns ``check_result``."""
    handler_stub = MagicMock()
    handler_stub.check_hosts_status.return_value = check_result

    config = K8sProviderConfig(namespace="orb-test")
    logger = MagicMock()

    registry = K8sHandlerRegistry(
        config=config,
        logger=logger,
        client_provider=MagicMock,
        watch_manager_provider=lambda: None,
        plugin_factories=lambda: {},
        native_spec_service_provider=lambda: None,
        handler_overrides={"Pod": handler_stub},
    )
    return registry


def _fulfilment(state: str = "in_progress") -> ProviderFulfilment:
    return ProviderFulfilment(state=state, message="test", target_units=1)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests — confirmed pods synthesised as terminated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirmed_missing_pod_is_synthesised_as_terminated() -> None:
    """A pod in provider_data['pod_names'] that is absent from the live list
    must be surfaced as ``status='terminated'``."""
    check_result = CheckHostsStatusResult(
        instances=[],  # pod is gone from the cluster
        fulfilment=_fulfilment("in_progress"),
    )
    registry = _make_registry(check_result=check_result)
    request = _make_acquire_request(pod_names=["orb-confirmed-0000"])

    outcome = await registry.get_status(["orb-confirmed-0000"], request)
    assert isinstance(outcome, (Accepted, Completed))
    instances: list[dict[str, Any]] = outcome.metadata["instances"]
    terminated = [i for i in instances if i["instance_id"] == "orb-confirmed-0000"]
    assert terminated, "Confirmed pod should appear in instances"
    assert terminated[0]["status"] == "terminated"


# ---------------------------------------------------------------------------
# Tests — phantom pods surfaced as unknown, not terminated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phantom_pod_not_in_confirmed_set_is_unknown() -> None:
    """An ID supplied in resource_ids but absent from provider_data['pod_names']
    must surface as status='unknown', not 'terminated'."""
    check_result = CheckHostsStatusResult(
        instances=[],
        fulfilment=_fulfilment("in_progress"),
    )
    registry = _make_registry(check_result=check_result)
    # pod_names is empty — the pod was never confirmed submitted.
    request = _make_acquire_request(pod_names=[])

    outcome = await registry.get_status(["orb-phantom-0000"], request)
    assert isinstance(outcome, (Accepted, Completed))
    instances: list[dict[str, Any]] = outcome.metadata["instances"]
    phantom = [i for i in instances if i["instance_id"] == "orb-phantom-0000"]
    assert phantom, "Phantom pod should still appear in instances"
    assert phantom[0]["status"] == "unknown", f"Expected 'unknown', got {phantom[0]['status']!r}"


@pytest.mark.asyncio
async def test_phantom_pod_does_not_complete_return_request() -> None:
    """A return request must NOT transition to Completed when only phantom IDs
    are missing from the live list (no confirmed pods present)."""
    check_result = CheckHostsStatusResult(
        instances=[],
        fulfilment=_fulfilment("in_progress"),
    )
    registry = _make_registry(check_result=check_result)
    # No confirmed pod_names — the resource_id is a phantom.
    request = _make_return_request(pod_names=[])

    outcome = await registry.get_status(["orb-phantom-0001"], request)
    # Return must stay Accepted (still draining), not Completed.
    assert isinstance(outcome, Accepted), (
        f"Expected Accepted (not Completed) but got {type(outcome).__name__}"
    )


@pytest.mark.asyncio
async def test_confirmed_pod_deletion_completes_return_request() -> None:
    """A return request with a confirmed pod (in pod_names) that is now absent
    from the live list must transition to Completed."""
    check_result = CheckHostsStatusResult(
        instances=[],  # pod is gone
        fulfilment=_fulfilment("in_progress"),
    )
    registry = _make_registry(check_result=check_result)
    request = _make_return_request(pod_names=["orb-real-0000"])

    outcome = await registry.get_status(["orb-real-0000"], request)
    assert isinstance(outcome, Completed), f"Expected Completed but got {type(outcome).__name__}"


@pytest.mark.asyncio
async def test_mixed_confirmed_and_phantom_ids() -> None:
    """When resource_ids contains both confirmed and phantom IDs, confirmed pods
    must be 'terminated' and phantoms must be 'unknown'."""
    check_result = CheckHostsStatusResult(
        instances=[],
        fulfilment=_fulfilment("in_progress"),
    )
    registry = _make_registry(check_result=check_result)
    # Only "orb-real-0000" is confirmed; "orb-phantom-0001" is not.
    request = _make_acquire_request(pod_names=["orb-real-0000"])

    outcome = await registry.get_status(["orb-real-0000", "orb-phantom-0001"], request)
    instances: list[dict[str, Any]] = outcome.metadata["instances"]
    by_id = {i["instance_id"]: i["status"] for i in instances}
    assert by_id["orb-real-0000"] == "terminated"
    assert by_id["orb-phantom-0001"] == "unknown"
