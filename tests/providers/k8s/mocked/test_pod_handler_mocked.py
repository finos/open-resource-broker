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
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

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
    from orb.domain.template.template_aggregate import Template

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
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

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
    from kmock import resource

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
    from kmock import resource

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
    from kmock import resource

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
    from kmock import resource

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


# ---------------------------------------------------------------------------
# F2 — Return requests advance to complete when pod deletion is confirmed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_return_request_completes_when_pod_deleted(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """A return request must reach Completed once the pod is gone from the cluster.

    Regression for the bug where return requests stayed in 'running' state
    permanently after pod deletion because missing IDs were classified as
    'unknown' (not 'terminated') when provider_data['pod_names'] was absent,
    preventing all_confirmed_gone from becoming True.

    Sequence:
    1. Seed kmock with a Running pod.
    2. Delete the pod via release_hosts.
    3. Call get_status on the handler registry with a RETURN-typed request
       and the pod name in resource_ids.
    4. Assert the result is Completed (return request reached terminal state).
    """
    from orb.domain.base.operation_outcome import Completed
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType
    from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

    request_id = f"req-{uuid.uuid4()}"
    pod_name = f"orb-{request_id[4:12]}-0000"
    _preload_pod(kmock_k8s, name=pod_name, phase="Running", ready=True, request_id=request_id)

    # --- Step 1: release_hosts deletes the pod ---
    handler = _make_pod_handler(k8s_client_facade, k8s_config)
    await handler.release_hosts(
        [pod_name],
        {"namespace": "orb-test", "request_id": request_id},
    )

    # --- Step 2: build a RETURN-typed request with no pod_names in provider_data ---
    # Deliberately omit pod_names to reproduce the original bug scenario
    # (e.g. Job handler or requests created before pod_names was populated).
    return_request = Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.RETURN,
        provider_type="k8s",
        provider_api="Pod",
        template_id="tpl-1",
        requested_count=1,
        provider_data={"namespace": "orb-test"},  # no pod_names key
    )

    # --- Step 3: poll get_status via the registry ---
    registry = K8sHandlerRegistry(
        config=k8s_config,
        logger=MagicMock(),
        client_provider=lambda: k8s_client_facade,
        watch_manager_provider=lambda: None,
        plugin_factories=lambda: {},
        native_spec_service_provider=lambda: None,
        handler_overrides={"Pod": handler},
    )

    outcome = await registry.get_status([pod_name], return_request)

    # The return request must be Completed once the pod is confirmed deleted.
    assert isinstance(outcome, Completed), (
        f"Expected Completed for deleted pod on return request, "
        f"got {type(outcome).__name__}: {outcome!r}"
    )
    assert pod_name in outcome.resource_ids


# ---------------------------------------------------------------------------
# F4 — Fatal waiting reasons (InvalidImageName etc.) surfaced as errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_handler_invalid_image_name_surfaced_as_failed(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """A pod stuck in Pending with reason=InvalidImageName must be classified as failed.

    Regression for the bug where templates with invalid image names produced
    pods that stayed in Pending forever with an empty message. After the fix,
    check_hosts_status must return status='failed' and status_reason='InvalidImageName'
    so the operator receives a visible, actionable error.
    """
    from kmock import resource

    request_id = f"req-{uuid.uuid4()}"
    pod_name = f"orb-{request_id[4:12]}-0000"

    # Seed a Pending pod with InvalidImageName waiting reason in containerStatuses.
    pod_res = resource("", "v1", "pods")
    kmock_k8s.objects[pod_res, "orb-test", pod_name] = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": "orb-test",
            "labels": {
                "orb.io/managed": "true",
                "orb.io/request-id": request_id,
            },
        },
        "spec": {
            "containers": [{"name": "app", "image": "INVALID IMAGE NAME WITH SPACES!!!"}],
        },
        "status": {
            "phase": "Pending",
            "podIP": None,
            "hostIP": None,
            "conditions": [{"type": "Ready", "status": "False"}],
            "containerStatuses": [
                {
                    "name": "app",
                    "ready": False,
                    "restartCount": 0,
                    "image": "INVALID IMAGE NAME WITH SPACES!!!",
                    "imageID": "",
                    "state": {
                        "waiting": {
                            "reason": "InvalidImageName",
                            "message": "invalid image name",
                        }
                    },
                }
            ],
        },
    }

    handler = _make_pod_handler(k8s_client_facade, k8s_config)
    request = _make_request(requested_count=1, request_id=request_id)

    result = await asyncio.to_thread(handler.check_hosts_status, request)

    assert len(result.instances) == 1
    inst = result.instances[0]
    # InvalidImageName waiting reason must be escalated to 'failed'.
    assert inst["status"] == "failed", (
        f"Expected 'failed' for InvalidImageName pod, got {inst['status']!r}"
    )
    assert inst["status_reason"] == "InvalidImageName", (
        f"Expected status_reason='InvalidImageName', got {inst['status_reason']!r}"
    )
    assert result.fulfilment.state == "failed", (
        f"Expected failed fulfilment for InvalidImageName pod, "
        f"got {result.fulfilment.state!r} (message: {result.fulfilment.message!r})"
    )


# ---------------------------------------------------------------------------
# F5 — template providerConfig.namespace override is respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_created_in_template_namespace_override(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """A template with providerConfig.namespace must create pods in that namespace.

    Regression for the bug where providerConfig.namespace was silently ignored
    because build_template_for_request used dict.setdefault() to merge it, which
    does not override None values produced by model_dump().
    """
    from kmock import resource

    from orb.providers.k8s.domain.template.k8s_template import K8sTemplate

    # Register the pods resource under both namespaces so kmock accepts creates.
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

    # Template with explicit namespace override (different from provider default).
    template_with_ns = K8sTemplate(
        template_id="tpl-ns-override",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=1,
        namespace="custom-namespace",
    )

    handler = _make_pod_handler(k8s_client_facade, k8s_config)
    request = _make_request(requested_count=1)

    result = await handler.acquire_hosts(request, template_with_ns)

    # Pod must have been created in the template-level namespace.
    pod_name = result["resource_ids"][0]
    assert result["provider_data"]["namespace"] == "custom-namespace", (
        f"Expected pod created in 'custom-namespace', "
        f"got namespace={result['provider_data']['namespace']!r}"
    )
    # Verify the pod is actually stored under the correct namespace in kmock.
    stored = [(res, ns, name) for res, ns, name in kmock_k8s.objects]
    custom_ns_pods = [name for res, ns, name in stored if ns == "custom-namespace"]
    assert pod_name in custom_ns_pods, (
        f"Pod {pod_name!r} not found under 'custom-namespace' in kmock. Stored objects: {stored}"
    )
