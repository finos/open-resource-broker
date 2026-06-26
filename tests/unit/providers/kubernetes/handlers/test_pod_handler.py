"""Unit tests for :class:`KubernetesPodHandler` (Phase B).

Mocks ``CoreV1Api`` so no cluster is required.  Covers:

* concurrent ``create_namespaced_pod`` calls in ``acquire_hosts``
* the pod-phase -> ORB status mapping in ``check_hosts_status``
* fulfilment verdict computation across the canonical states
* selective ``release_hosts`` and best-effort 404 handling
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.domain.template.template_aggregate import Template
from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
from orb.providers.kubernetes.handlers.pod_handler import KubernetesPodHandler

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_request(*, requested_count: int = 2, request_id: str | None = None) -> Request:
    return Request(
        request_id=RequestId(value=request_id or f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="kubernetes",
        provider_api="KubernetesPod",
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data={"namespace": "orb-test"},
    )


def _make_template() -> Template:
    return Template(
        template_id="tpl-1",
        provider_type="kubernetes",
        provider_api="KubernetesPod",
        image_id="busybox:latest",
        max_instances=4,
        provider_data={
            "kubernetes": {
                "namespace": "orb-test",
                "container_image": "busybox:latest",
                "resource_requests": {"cpu": "100m", "memory": "64Mi"},
            }
        },
    )


def _make_handler(core_v1_mock: Any) -> KubernetesPodHandler:
    client = MagicMock()
    client.core_v1 = core_v1_mock
    config = KubernetesProviderConfig(namespace="orb-test")
    logger = MagicMock()
    return KubernetesPodHandler(
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
        ("Pending", False, "ImagePullBackOff", None, "pending", "ImagePullBackOff"),
        ("Pending", False, None, "Unschedulable", "pending", "Unschedulable"),
        ("Running", False, None, None, "starting", None),
        ("Running", True, None, None, "running", None),
        ("Succeeded", False, None, None, "running", None),
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
    assert inst["provider_api"] == "KubernetesPod"


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
    # 1 running + 1 failed, neither pending — partial.
    assert result.fulfilment.state == "partial"
    assert result.fulfilment.running_count == 1
    assert result.fulfilment.failed_count == 1


def test_check_hosts_status_handles_empty_list() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    handler = _make_handler(core_v1)
    request = _make_request(requested_count=2)

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "in_progress"
    assert result.instances == []


def test_check_hosts_status_list_failure_returns_in_progress() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.side_effect = RuntimeError("apiserver unavailable")
    handler = _make_handler(core_v1)
    handler._max_retries = 1
    request = _make_request(requested_count=2)

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "in_progress"
    assert "apiserver unavailable" in result.fulfilment.message


# ---------------------------------------------------------------------------
# release_hosts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_hosts_deletes_named_pods() -> None:
    core_v1 = MagicMock()
    core_v1.delete_namespaced_pod.return_value = SimpleNamespace()
    handler = _make_handler(core_v1)
    request = _make_request(requested_count=2)

    await handler.release_hosts(["orb-aaa-0000", "orb-aaa-0001"], request)
    assert core_v1.delete_namespaced_pod.call_count == 2


@pytest.mark.asyncio
async def test_release_hosts_swallows_404() -> None:
    # Build a fake ApiException with status=404 to mimic the kubernetes SDK.
    from kubernetes.client.exceptions import ApiException

    core_v1 = MagicMock()

    def _raise_404(*, name: str, namespace: str) -> None:
        raise ApiException(status=404, reason="Not Found")

    core_v1.delete_namespaced_pod.side_effect = _raise_404
    handler = _make_handler(core_v1)
    handler._max_retries = 1
    request = _make_request()

    # Must not raise — 404 is best-effort.
    await handler.release_hosts(["orb-aaa-0000"], request)
    assert core_v1.delete_namespaced_pod.call_count == 1


@pytest.mark.asyncio
async def test_release_hosts_propagates_non_404_errors() -> None:
    from orb.infrastructure.resilience.exceptions import MaxRetriesExceededError

    core_v1 = MagicMock()
    core_v1.delete_namespaced_pod.side_effect = RuntimeError("server down")
    handler = _make_handler(core_v1)
    handler._max_retries = 1
    request = _make_request()

    # Non-404 errors fall through to retry-with-backoff and surface as
    # MaxRetriesExceededError once the retry budget is exhausted.
    with pytest.raises((RuntimeError, MaxRetriesExceededError)):
        await handler.release_hosts(["orb-aaa-0000"], request)


@pytest.mark.asyncio
async def test_release_hosts_with_empty_list_is_noop() -> None:
    core_v1 = MagicMock()
    handler = _make_handler(core_v1)
    request = _make_request()

    await handler.release_hosts([], request)
    core_v1.delete_namespaced_pod.assert_not_called()


# ---------------------------------------------------------------------------
# Namespace resolution
# ---------------------------------------------------------------------------


def test_resolve_namespace_template_override_wins() -> None:
    core_v1 = MagicMock()
    handler = _make_handler(core_v1)
    template = _make_template()
    ns = handler.resolve_namespace(template)
    assert ns == "orb-test"


def test_resolve_namespace_rejects_namespace_outside_allowlist() -> None:
    client = MagicMock()
    config = KubernetesProviderConfig(namespace="orb", namespaces=["allowed-a", "allowed-b"])
    handler = KubernetesPodHandler(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
    )
    template = Template(
        template_id="tpl",
        provider_type="kubernetes",
        provider_api="KubernetesPod",
        image_id="busybox",
        max_instances=1,
        provider_data={"kubernetes": {"namespace": "orb"}},
    )
    with pytest.raises(ValueError, match="not in the provider's configured namespaces"):
        handler.resolve_namespace(template)


def test_resolve_namespace_accepts_wildcard_list() -> None:
    client = MagicMock()
    config = KubernetesProviderConfig(namespace="orb", namespaces=["*"])
    handler = KubernetesPodHandler(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
    )
    template = Template(
        template_id="tpl",
        provider_type="kubernetes",
        provider_api="KubernetesPod",
        image_id="busybox",
        max_instances=1,
        provider_data={"kubernetes": {"namespace": "any-ns"}},
    )
    assert handler.resolve_namespace(template) == "any-ns"


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------


def test_get_example_templates_returns_pod_example() -> None:
    examples = KubernetesPodHandler.get_example_templates()
    assert len(examples) >= 1
    example = examples[0]
    assert example.provider_api == "KubernetesPod"
    assert example.provider_type == "kubernetes"
    assert example.image_id == "busybox:latest"


# ---------------------------------------------------------------------------
# Misc — semaphore caps concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_hosts_respects_concurrent_creates_cap() -> None:
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def _bookkeeping_create(*, namespace: str, body: Any) -> Any:
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        async with lock:
            in_flight -= 1
        return SimpleNamespace()

    # The handler wraps the SDK call in asyncio.to_thread; for this test we
    # bypass the wrap by replacing _create_one_pod with our async tracker.
    client = MagicMock()
    client.core_v1 = MagicMock()
    handler = KubernetesPodHandler(
        kubernetes_client=client,
        config=KubernetesProviderConfig(namespace="orb-test"),
        logger=MagicMock(),
        max_concurrent_creates=3,
    )

    async def _stub(*, sem: asyncio.Semaphore, namespace: str, pod_name: str, body: Any) -> str:
        async with sem:
            await _bookkeeping_create(namespace=namespace, body=body)
        return pod_name

    handler._create_one_pod = _stub  # type: ignore[method-assign]

    request = _make_request(requested_count=10)
    template = _make_template()
    await handler.acquire_hosts(request, template)
    assert max_in_flight <= 3
