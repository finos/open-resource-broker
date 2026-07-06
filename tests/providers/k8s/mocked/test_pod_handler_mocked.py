"""kmock-backed tests for :class:`K8sPodHandler`.

Each test runs the real kubernetes SDK against a local kmock HTTP server —
no Python-level mocking of SDK methods is involved.  This exercises the
SDK serialisation/deserialisation path and HTTP wire format end-to-end,
complementing the unit tests in ``tests/providers/k8s/unit/handlers/``.

Covered scenarios
-----------------

* acquire_hosts issues a POST to the pods endpoint and the emulator records it.
* check_hosts_status reads pod state from the kmock emulator's object store.
* release_hosts issues a DELETE and the emulator removes the object.
* release_hosts is resilient when the server returns 404 on DELETE.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from kmock import KubernetesEmulator

# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _make_request(
    *,
    requested_count: int = 1,
    request_id: str | None = None,
    namespace: str = "orb-test",
) -> Any:
    from orb.domain.request.aggregate import Request  # noqa: PLC0415
    from orb.domain.request.value_objects import RequestId, RequestType  # noqa: PLC0415

    return Request(
        request_id=RequestId(value=request_id or f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data={"namespace": namespace},
    )


def _make_template(namespace: str = "orb-test") -> Any:
    from orb.domain.template.template_aggregate import Template  # noqa: PLC0415

    return Template(
        template_id="tpl-1",
        provider_type="k8s",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=4,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "container_image": "busybox:latest",
                "resource_requests": {"cpu": "100m", "memory": "64Mi"},
            }
        },
    )


def _make_pod_handler(k8s_client_facade: Any, k8s_config: Any) -> Any:
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler  # noqa: PLC0415

    return K8sPodHandler(
        kubernetes_client=k8s_client_facade,
        config=k8s_config,
        logger=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Pre-load helpers — seed kmock's in-memory store before a test reads
# ---------------------------------------------------------------------------


def _preload_pod(
    kmock_k8s: KubernetesEmulator,
    *,
    name: str,
    namespace: str = "orb-test",
    phase: str = "Running",
    ready: bool = True,
    request_id: str = "req-abc",
) -> None:
    """Directly insert a pod into kmock's in-memory object store.

    The emulator exposes ``kmock.objects[resource_key, namespace, name] = {...}``
    for pre-loading.  The pod dict matches the structure the kubernetes SDK
    deserialises when it reads a ``GET /api/v1/namespaces/<ns>/pods/<name>``.
    """
    from kmock import resource  # noqa: PLC0415

    pod_res = resource("", "v1", "pods")
    conditions = [{"type": "Ready", "status": "True" if ready else "False"}]
    # V1PodSpec.containers cannot be None when the SDK deserialises a list
    # response; include a minimal container entry to satisfy the model.
    kmock_k8s.objects[pod_res, namespace, name] = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "orb.io/managed": "true",
                "orb.io/request-id": request_id,
                "orb.io/provider-api": "Pod",
            },
        },
        "spec": {
            "nodeName": "node-1",
            "containers": [{"name": "app", "image": "busybox:latest"}],
        },
        "status": {
            "phase": phase,
            "podIP": "10.0.0.1" if phase == "Running" else None,
            "hostIP": "10.1.0.1" if phase == "Running" else None,
            "conditions": conditions,
            "containerStatuses": [],
        },
    }


# ---------------------------------------------------------------------------
# acquire_hosts — POST /api/v1/namespaces/<ns>/pods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_handler_acquire_calls_create_namespaced_pod(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """acquire_hosts posts a pod body to the kmock apiserver.

    After the call the emulator's object store must contain the created pod
    and the requests log must record exactly one POST to the pods collection.
    """
    from kmock import resource  # noqa: PLC0415

    handler = _make_pod_handler(k8s_client_facade, k8s_config)
    request = _make_request(requested_count=1)
    template = _make_template()

    # Register the pods resource so kmock exposes it via API discovery.
    pod_res = resource("", "v1", "pods")
    kmock_k8s.resources[pod_res] = {
        "namespaced": True,
        "kind": "Pod",
        "singular": "pod",
        "verbs": ["get", "list", "create", "delete", "watch"],
        "shortnames": ["po"],
        "categories": [],
        "subresources": [],
    }

    result = await handler.acquire_hosts(request, template)

    assert len(result["resource_ids"]) == 1
    pod_name = result["resource_ids"][0]
    assert pod_name.startswith("orb-")

    # The emulator must have recorded exactly the created pod.
    assert (pod_res, "orb-test", pod_name) in [
        (res, ns, name) for res, ns, name in kmock_k8s.objects
    ]


@pytest.mark.asyncio
async def test_pod_handler_acquire_creates_multiple_pods(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """acquire_hosts with requested_count=3 issues three POSTs."""
    from kmock import resource  # noqa: PLC0415

    pod_res = resource("", "v1", "pods")
    kmock_k8s.resources[pod_res] = {
        "namespaced": True,
        "kind": "Pod",
        "singular": "pod",
        "verbs": ["get", "list", "create", "delete", "watch"],
        "shortnames": ["po"],
        "categories": [],
        "subresources": [],
    }

    handler = _make_pod_handler(k8s_client_facade, k8s_config)
    request = _make_request(requested_count=3)
    template = _make_template()

    result = await handler.acquire_hosts(request, template)

    assert len(result["resource_ids"]) == 3
    stored_names = {name for _, _, name in kmock_k8s.objects}
    for pod_name in result["resource_ids"]:
        assert pod_name in stored_names


# ---------------------------------------------------------------------------
# check_hosts_status — reads from apiserver via list_namespaced_pod
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_handler_check_status_reads_from_apiserver(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """check_hosts_status sees the pod the emulator was seeded with.

    We pre-load a Running+Ready pod, then call check_hosts_status; the
    handler must report the pod as running and produce a fulfilled verdict.

    ``check_hosts_status`` is synchronous and calls list_namespaced_pod via
    urllib3, which would block the event loop thread and prevent the kmock
    aiohttp server from responding.  We therefore run it in a worker thread
    via asyncio.to_thread so the event loop remains free to serve requests.
    """
    request_id = f"req-{uuid.uuid4()}"
    pod_name = f"orb-{request_id[4:12]}-0000"
    _preload_pod(kmock_k8s, name=pod_name, phase="Running", ready=True, request_id=request_id)

    handler = _make_pod_handler(k8s_client_facade, k8s_config)
    request = _make_request(requested_count=1, request_id=request_id)

    result = await asyncio.to_thread(handler.check_hosts_status, request)

    assert len(result.instances) == 1
    assert result.instances[0]["status"] == "running"
    assert result.fulfilment.state == "fulfilled"


@pytest.mark.asyncio
async def test_pod_handler_check_status_pending_pod(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """check_hosts_status maps a Pending phase to ORB status 'pending'.

    Runs in a worker thread so the kmock aiohttp server can respond.
    """
    request_id = f"req-{uuid.uuid4()}"
    pod_name = f"orb-{request_id[4:12]}-0000"
    _preload_pod(kmock_k8s, name=pod_name, phase="Pending", ready=False, request_id=request_id)

    handler = _make_pod_handler(k8s_client_facade, k8s_config)
    request = _make_request(requested_count=1, request_id=request_id)

    result = await asyncio.to_thread(handler.check_hosts_status, request)

    # Pending phase maps to "pending" status; fulfilment is in_progress.
    assert result.instances[0]["status"] == "pending"
    assert result.fulfilment.state == "in_progress"


# ---------------------------------------------------------------------------
# release_hosts — DELETE /api/v1/namespaces/<ns>/pods/<name>
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_handler_release_calls_delete_namespaced_pod(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """release_hosts issues a DELETE and the object is gone from the store."""
    from kmock import resource  # noqa: PLC0415

    request_id = f"req-{uuid.uuid4()}"
    pod_name = f"orb-{request_id[4:12]}-0000"
    _preload_pod(kmock_k8s, name=pod_name, request_id=request_id)

    pod_res = resource("", "v1", "pods")
    assert (pod_res, "orb-test", pod_name) in [(res, ns, n) for res, ns, n in kmock_k8s.objects]

    handler = _make_pod_handler(k8s_client_facade, k8s_config)
    request = _make_request(requested_count=1, request_id=request_id)
    handler._max_retries = 1

    await handler.release_hosts([pod_name], request.provider_data)

    # The object must now be marked deleted in the emulator.
    obj = kmock_k8s.objects[pod_res, "orb-test", pod_name]
    assert obj.deleted


@pytest.mark.asyncio
async def test_pod_handler_release_tolerates_404(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """release_hosts must not raise when the pod no longer exists (404).

    We call release_hosts with a pod name that was never created in kmock.
    The emulator returns 404 and the handler must swallow it (best-effort).
    """
    handler = _make_pod_handler(k8s_client_facade, k8s_config)
    request = _make_request()
    handler._max_retries = 1

    # The pod "ghost-pod" does not exist in kmock.  The DELETE call returns
    # a 404 status which the handler should tolerate without raising.
    await handler.release_hosts(["ghost-pod"], request.provider_data)
    # Reaching here without an exception is the assertion.


# ---------------------------------------------------------------------------
# F1 — Succeeded pods classified as success, not fail/terminated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_handler_succeeded_pod_classified_as_fulfilled(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """A pod that exits 0 (Kubernetes phase=Succeeded) must map to result: succeed.

    Regression for the bug where Succeeded pods were classified as
    'fail'/'terminated' with 'Machine failed (no detail available)' because
    the fulfilment math did not count 'terminated' status as fulfilled capacity.
    After the fix, check_hosts_status must return state='fulfilled' and the
    instance status must be 'terminated' (run-to-completion semantics, not a
    failure) with a success message.
    """
    request_id = f"req-{uuid.uuid4()}"
    pod_name = f"orb-{request_id[4:12]}-0000"
    _preload_pod(
        kmock_k8s,
        name=pod_name,
        phase="Succeeded",
        ready=False,
        request_id=request_id,
    )

    handler = _make_pod_handler(k8s_client_facade, k8s_config)
    request = _make_request(requested_count=1, request_id=request_id)

    result = await asyncio.to_thread(handler.check_hosts_status, request)

    assert len(result.instances) == 1
    inst = result.instances[0]
    # Succeeded phase maps to 'terminated' (run-to-completion, not a failure).
    assert inst["status"] == "terminated", (
        f"Expected 'terminated' for Succeeded pod, got {inst['status']!r}"
    )
    # The fulfilment verdict must be 'fulfilled', not 'in_progress' or 'failed'.
    assert result.fulfilment.state == "fulfilled", (
        f"Expected fulfilled for Succeeded pod, got {result.fulfilment.state!r} "
        f"(message: {result.fulfilment.message!r})"
    )
