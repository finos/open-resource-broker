"""Unit tests for :class:`K8sPodHandler`.

Mocks ``CoreV1Api`` so no cluster is required.  Covers:

* concurrent ``create_namespaced_pod`` calls in ``acquire_hosts``
* the pod-phase -> ORB status mapping in ``check_hosts_status``
* fulfilment verdict computation across the canonical states
* selective ``release_hosts`` and best-effort 404 handling
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_request(*, requested_count: int = 2, request_id: str | None = None) -> Request:
    return Request(
        request_id=RequestId(value=request_id or f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data={"namespace": "orb-test"},
    )


def _make_template() -> Template:
    return Template(
        template_id="tpl-1",
        provider_type="k8s",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=4,
        provider_data={
            "k8s": {
                "namespace": "orb-test",
                "container_image": "busybox:latest",
                "resource_requests": {"cpu": "100m", "memory": "64Mi"},
            }
        },
    )


def _make_handler(core_v1_mock: Any) -> K8sPodHandler:
    client = MagicMock()
    client.core_v1 = core_v1_mock
    config = K8sProviderConfig(namespace="orb-test")
    logger = MagicMock()
    return K8sPodHandler(
        kubernetes_client=client,
        config=config,
        logger=logger,
    )


def _make_pod(
    *,
    name: str,
    phase: str,
    ready: bool = False,
    container_reason: str | None = None,
    condition_reason: str | None = None,
) -> SimpleNamespace:
    conditions: list[SimpleNamespace] = []
    if ready:
        conditions.append(SimpleNamespace(type="Ready", status="True", reason=None))
    else:
        conditions.append(SimpleNamespace(type="Ready", status="False", reason=None))
    if condition_reason:
        conditions.append(
            SimpleNamespace(type="PodScheduled", status="False", reason=condition_reason)
        )

    container_statuses: list[SimpleNamespace] = []
    if container_reason:
        if phase == "Failed":
            state = SimpleNamespace(
                terminated=SimpleNamespace(reason=container_reason),
                waiting=None,
            )
        else:
            state = SimpleNamespace(
                terminated=None,
                waiting=SimpleNamespace(reason=container_reason),
            )
        container_statuses.append(SimpleNamespace(state=state))

    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace="orb-test",
            labels={"orb.io/request-id": "req-abc"},
        ),
        spec=SimpleNamespace(node_name="node-1"),
        status=SimpleNamespace(
            phase=phase,
            pod_ip="10.0.0.1" if phase == "Running" else None,
            host_ip="10.1.0.1" if phase == "Running" else None,
            start_time=None,
            conditions=conditions,
            container_statuses=container_statuses,
        ),
    )


# ---------------------------------------------------------------------------
# acquire_hosts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_hosts_creates_n_pods_concurrently() -> None:
    core_v1 = MagicMock()
    core_v1.create_namespaced_pod.return_value = SimpleNamespace()
    handler = _make_handler(core_v1)
    request = _make_request(requested_count=3)
    template = _make_template()

    result = await handler.acquire_hosts(request, template)

    assert core_v1.create_namespaced_pod.call_count == 3
    assert result["resource_ids"] == result["machine_ids"]
    assert len(result["resource_ids"]) == 3
    # All names follow the orb-<prefix>-NNNN pattern.
    for name in result["resource_ids"]:
        assert name.startswith("orb-")
    # provider_data carries the namespace and the created pod names.
    assert result["provider_data"]["namespace"] == "orb-test"
    assert result["provider_data"]["pod_names"] == result["resource_ids"]


@pytest.mark.asyncio
async def test_acquire_hosts_partial_failure_still_reports_successes() -> None:
    core_v1 = MagicMock()

    # Fail one specific pod name; the rest succeed.  Using the pod name (rather
    # than a call counter) keeps the test deterministic against the retry layer.
    def _create(*, namespace: str, body: Any) -> Any:
        pod_name = body.metadata.name if hasattr(body, "metadata") else ""
        if pod_name.endswith("-0001"):
            raise RuntimeError("boom")
        return SimpleNamespace()

    core_v1.create_namespaced_pod.side_effect = _create
    handler = _make_handler(core_v1)
    # Disable retry so the deterministic failure is not masked.
    handler._max_retries = 1

    request = _make_request(requested_count=3)
    template = _make_template()
    result = await handler.acquire_hosts(request, template)

    assert len(result["resource_ids"]) == 2
    assert len(result["provider_data"]["failed_pod_names"]) == 1
    assert result["provider_data"]["failed_pod_names"][0].endswith("-0001")


@pytest.mark.asyncio
async def test_acquire_hosts_all_failures_raises() -> None:
    core_v1 = MagicMock()
    core_v1.create_namespaced_pod.side_effect = RuntimeError("boom")
    handler = _make_handler(core_v1)
    handler._max_retries = 1

    request = _make_request(requested_count=2)
    template = _make_template()

    with pytest.raises(RuntimeError, match="All pod creates failed"):
        await handler.acquire_hosts(request, template)


# ---------------------------------------------------------------------------
# check_hosts_status — phase -> status mapping + fulfilment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phase,ready,container_reason,condition_reason,expected_status,expected_reason",
    [
        ("Pending", False, None, None, "pending", None),
        # Fatal waiting reasons are escalated to 'failed' so the error is visible.
        ("Pending", False, "ImagePullBackOff", None, "failed", "ImagePullBackOff"),
        ("Pending", False, None, "Unschedulable", "pending", "Unschedulable"),
        ("Running", False, None, None, "starting", None),
        ("Running", True, None, None, "running", None),
        ("Succeeded", False, None, None, "terminated", "Container completed successfully"),
        ("Failed", False, "OOMKilled", None, "failed", "OOMKilled"),
        ("Failed", False, "Error", None, "failed", "Error"),
        ("Unknown", False, None, None, "pending", None),
    ],
)
def test_pod_phase_to_status_mapping(
    phase: str,
    ready: bool,
    container_reason: str | None,
    condition_reason: str | None,
    expected_status: str,
    expected_reason: str | None,
) -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(
                name="orb-aaa-0000",
                phase=phase,
                ready=ready,
                container_reason=container_reason,
                condition_reason=condition_reason,
            )
        ]
    )
    handler = _make_handler(core_v1)
    request = _make_request(requested_count=1)

    result = handler.check_hosts_status(request)
    assert isinstance(result, CheckHostsStatusResult)
    assert len(result.instances) == 1
    inst = result.instances[0]
    assert inst["status"] == expected_status
    assert inst["status_reason"] == expected_reason
    assert inst["provider_api"] == "Pod"


def test_check_hosts_status_fulfilled() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-aaa-0000", phase="Running", ready=True),
            _make_pod(name="orb-aaa-0001", phase="Running", ready=True),
        ]
    )
    handler = _make_handler(core_v1)
    request = _make_request(requested_count=2)

    result = handler.check_hosts_status(request)
    assert isinstance(result.fulfilment, ProviderFulfilment)
    assert result.fulfilment.state == "fulfilled"
    assert result.fulfilment.running_count == 2
    assert result.fulfilment.target_units == 2


def test_check_hosts_status_in_progress() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-aaa-0000", phase="Running", ready=True),
            _make_pod(name="orb-aaa-0001", phase="Pending"),
        ]
    )
    handler = _make_handler(core_v1)
    request = _make_request(requested_count=2)

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "in_progress"
    assert result.fulfilment.running_count == 1
    assert result.fulfilment.pending_count == 1


def test_check_hosts_status_failed_all() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-aaa-0000", phase="Failed", container_reason="OOMKilled"),
        ]
    )
    handler = _make_handler(core_v1)
    request = _make_request(requested_count=1)

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "failed"
    assert result.fulfilment.failed_count == 1


def test_check_hosts_status_partial() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-aaa-0000", phase="Running", ready=True),
            _make_pod(name="orb-aaa-0001", phase="Failed"),
        ]
    )
    handler = _make_handler(core_v1)
    request = _make_request(requested_count=2)

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "partial"
    assert result.fulfilment.running_count == 1
    assert result.fulfilment.failed_count == 1


def test_check_hosts_status_api_error_returns_in_progress() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.side_effect = RuntimeError("connection refused")
    handler = _make_handler(core_v1)
    handler._max_retries = 1
    request = _make_request(requested_count=2)

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "in_progress"
    assert "will retry" in result.fulfilment.message


# ---------------------------------------------------------------------------
# release_hosts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_hosts_deletes_pods_by_name() -> None:
    core_v1 = MagicMock()
    core_v1.delete_namespaced_pod.return_value = SimpleNamespace()
    handler = _make_handler(core_v1)
    handler._max_retries = 1

    result = await handler.release_hosts(
        ["orb-aaa-0000", "orb-aaa-0001"],
        {"namespace": "orb-test"},
    )

    assert core_v1.delete_namespaced_pod.call_count == 2
    assert set(result["deleted"]) == {"orb-aaa-0000", "orb-aaa-0001"}
    assert result["failed_deletes"] == []


@pytest.mark.asyncio
async def test_release_hosts_tolerates_404() -> None:
    from kubernetes.client.exceptions import ApiException  # noqa: PLC0415

    core_v1 = MagicMock()
    core_v1.delete_namespaced_pod.side_effect = ApiException(status=404)
    handler = _make_handler(core_v1)
    handler._max_retries = 1

    # 404 must not raise — pod already gone is considered a success.
    result = await handler.release_hosts(["ghost-pod"], {"namespace": "orb-test"})
    assert result["failed_deletes"] == []


@pytest.mark.asyncio
async def test_release_hosts_all_failures_raises() -> None:
    core_v1 = MagicMock()
    core_v1.delete_namespaced_pod.side_effect = RuntimeError("boom")
    handler = _make_handler(core_v1)
    handler._max_retries = 1

    with pytest.raises(RuntimeError, match="All pod deletes failed"):
        await handler.release_hosts(["pod-a"], {"namespace": "orb-test"})


@pytest.mark.asyncio
async def test_release_hosts_partial_failure_logged() -> None:
    core_v1 = MagicMock()

    def _delete(*, name: str, namespace: str) -> Any:
        if name == "orb-bad-0001":
            raise RuntimeError("boom")
        return SimpleNamespace()

    core_v1.delete_namespaced_pod.side_effect = _delete
    handler = _make_handler(core_v1)
    handler._max_retries = 1

    result = await handler.release_hosts(
        ["orb-ok-0000", "orb-bad-0001"],
        {"namespace": "orb-test"},
    )
    assert "orb-ok-0000" in result["deleted"]
    assert any(name == "orb-bad-0001" for name, _ in result["failed_deletes"])


@pytest.mark.asyncio
async def test_release_hosts_no_machine_ids_is_noop() -> None:
    core_v1 = MagicMock()
    handler = _make_handler(core_v1)

    result = await handler.release_hosts([], {"namespace": "orb-test"})
    assert result == {"deleted": [], "failed_deletes": []}
    core_v1.delete_namespaced_pod.assert_not_called()


# ---------------------------------------------------------------------------
# F1 — Succeeded pods count as fulfilled capacity
# ---------------------------------------------------------------------------


def test_succeeded_pod_maps_to_fulfilled_fulfilment() -> None:
    """A bare Pod in Succeeded phase (exited 0) must produce state=fulfilled.

    Regression: compute_fulfilment did not count 'terminated' instances
    (the ORB status for Succeeded bare pods) so all-succeeded sets
    fell through to the final in_progress fallback.
    """
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[_make_pod(name="orb-aaa-0000", phase="Succeeded")]
    )
    handler = _make_handler(core_v1)
    request = _make_request(requested_count=1)

    result = handler.check_hosts_status(request)
    assert result.instances[0]["status"] == "terminated"
    assert result.fulfilment.state == "fulfilled", (
        f"Expected fulfilled for Succeeded pod, got {result.fulfilment.state!r}"
    )
