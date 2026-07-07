"""kmock-backed tests for the startup reconciler and orphan GC.

Both components use the real kubernetes SDK to issue list_namespaced_pod
calls.  These calls go through the real ApiClient against the kmock
HTTP server, exercising the full HTTP+JSON stack without a live cluster.

Covered scenarios
-----------------

* StartupReconciler populates PodStateCache from a kmock pod list.
* OrphanGarbageCollector identifies an orphan pod (no matching request id
  in storage) from a kmock pod list.
* OrphanGarbageCollector issues a DELETE call to remove an orphan when
  auto_cleanup_orphans is True.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from kmock import KubernetesEmulator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _preload_pod(
    kmock_k8s: KubernetesEmulator,
    *,
    name: str,
    namespace: str = "orb-test",
    request_id: str,
    phase: str = "Running",
    ready: bool = True,
) -> None:
    """Seed kmock with a pod carrying ORB labels."""
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


def _register_pods_resource(kmock_k8s: KubernetesEmulator) -> None:
    """Register the v1/pods resource so kmock API discovery works."""
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


# ---------------------------------------------------------------------------
# StartupReconciler — test_startup_reconciler_populates_cache_from_kmock_pod_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_reconciler_populates_cache_from_kmock_pod_list(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """StartupReconciler warms PodStateCache from the kmock pod list.

    We seed kmock with two pods belonging to a known request id.  After
    running the reconciler both pods must appear in the cache.

    StartupReconciler.run() calls list_namespaced_pod synchronously.  We run
    it in a worker thread via asyncio.to_thread so the event loop remains
    free to serve kmock's aiohttp responses.
    """
    from orb.providers.k8s.reconciliation.startup_reconciler import (
        StartupReconciler,  # noqa: PLC0415
    )
    from orb.providers.k8s.watch.pod_state_cache import PodStateCache  # noqa: PLC0415

    _register_pods_resource(kmock_k8s)

    request_id = f"req-{uuid.uuid4()}"
    _preload_pod(kmock_k8s, name="orb-warm-0000", request_id=request_id)
    _preload_pod(kmock_k8s, name="orb-warm-0001", request_id=request_id)

    cache = PodStateCache()
    reconciler = StartupReconciler(
        kubernetes_client=k8s_client_facade,
        config=k8s_config,
        cache=cache,
        logger=MagicMock(),
        known_request_ids=lambda: [request_id],
    )

    report = await asyncio.to_thread(reconciler.run)

    assert report.completed, f"Reconciler did not complete cleanly: {report.error}"
    assert report.pods_seen == 2
    assert report.pods_adopted == 2
    assert report.orphan_count == 0
    assert report.requests_warmed == 1

    states = cache.get(request_id) or []
    pod_names = {s.pod_name for s in states}
    assert pod_names == {"orb-warm-0000", "orb-warm-0001"}


@pytest.mark.asyncio
async def test_startup_reconciler_classifies_unknown_pod_as_orphan(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """StartupReconciler classifies a pod with an unknown request-id as an orphan.

    The pod carries the orb.io/managed label but its request-id is not in
    the ``known_request_ids`` set — it must appear in report.orphans.
    Runs in a worker thread to avoid blocking the event loop.
    """
    from orb.providers.k8s.reconciliation.startup_reconciler import (
        StartupReconciler,  # noqa: PLC0415
    )
    from orb.providers.k8s.watch.pod_state_cache import PodStateCache  # noqa: PLC0415

    _register_pods_resource(kmock_k8s)

    orphan_request_id = f"req-{uuid.uuid4()}"
    _preload_pod(kmock_k8s, name="orb-orphan-0000", request_id=orphan_request_id)

    cache = PodStateCache()
    reconciler = StartupReconciler(
        kubernetes_client=k8s_client_facade,
        config=k8s_config,
        cache=cache,
        logger=MagicMock(),
        # Provide an empty known set so every pod is an orphan.
        known_request_ids=lambda: [],
    )

    report = await asyncio.to_thread(reconciler.run)

    assert report.completed
    assert report.pods_seen == 1
    assert report.orphan_count == 1
    assert report.orphans[0].pod_name == "orb-orphan-0000"
    assert report.pods_adopted == 0


# ---------------------------------------------------------------------------
# OrphanGarbageCollector — test_orphan_gc_deletes_orphan_via_kmock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphan_gc_detects_orphan_pod(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """OrphanGC identifies a pod with no matching request-id as an orphan.

    auto_cleanup_orphans is False so the GC only logs — no DELETE issued.
    We verify the returned orphan list contains the expected pod name.
    """
    from orb.providers.k8s.configuration.config import K8sProviderConfig  # noqa: PLC0415
    from orb.providers.k8s.reconciliation.orphan_gc import OrphanGarbageCollector  # noqa: PLC0415

    _register_pods_resource(kmock_k8s)

    orphan_id = str(uuid.uuid4())
    _preload_pod(kmock_k8s, name="orb-orphan-gc-0000", request_id=orphan_id)

    config = K8sProviderConfig(namespace="orb-test", auto_cleanup_orphans=False)  # type: ignore[call-arg]
    gc = OrphanGarbageCollector(
        kubernetes_client=k8s_client_facade,
        config=config,
        logger=MagicMock(),
        known_request_ids=lambda: [],
        interval_seconds=9999,
    )

    orphans = await gc.run_once()

    assert len(orphans) == 1
    assert orphans[0].pod_name == "orb-orphan-gc-0000"


@pytest.mark.asyncio
async def test_orphan_gc_deletes_orphan_via_kmock(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """OrphanGC issues a DELETE for an orphan when auto_cleanup_orphans=True.

    We seed kmock with an orphan pod, run the GC with auto_cleanup_orphans
    enabled, and verify the pod is gone from the emulator's object store.
    """
    from kmock import resource  # noqa: PLC0415

    from orb.providers.k8s.configuration.config import K8sProviderConfig  # noqa: PLC0415
    from orb.providers.k8s.reconciliation.orphan_gc import OrphanGarbageCollector  # noqa: PLC0415

    _register_pods_resource(kmock_k8s)

    orphan_id = str(uuid.uuid4())
    pod_name = "orb-orphan-del-0000"
    _preload_pod(kmock_k8s, name=pod_name, request_id=orphan_id)

    pod_res = resource("", "v1", "pods")
    assert not kmock_k8s.objects[pod_res, "orb-test", pod_name].deleted

    config = K8sProviderConfig(  # type: ignore[call-arg]
        namespace="orb-test",
        auto_cleanup_orphans=True,
        orphan_min_age_seconds=0,  # skip age guard so the test pod is eligible
    )
    gc = OrphanGarbageCollector(
        kubernetes_client=k8s_client_facade,
        config=config,
        logger=MagicMock(),
        known_request_ids=lambda: [],
        interval_seconds=9999,
    )

    orphans = await gc.run_once()

    assert len(orphans) == 1
    assert orphans[0].pod_name == pod_name

    # The emulator must have recorded the DELETE — the object is now deleted.
    stored = kmock_k8s.objects[pod_res, "orb-test", pod_name]
    assert stored.deleted, "Expected orphan pod to be deleted from kmock store"


@pytest.mark.asyncio
async def test_orphan_gc_skips_known_pod(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """OrphanGC does not touch pods whose request-id is in the known set."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig  # noqa: PLC0415
    from orb.providers.k8s.reconciliation.orphan_gc import OrphanGarbageCollector  # noqa: PLC0415

    _register_pods_resource(kmock_k8s)

    known_id = str(uuid.uuid4())
    _preload_pod(kmock_k8s, name="orb-known-0000", request_id=known_id)

    config = K8sProviderConfig(  # type: ignore[call-arg]
        namespace="orb-test",
        auto_cleanup_orphans=True,
        orphan_min_age_seconds=0,
    )
    gc = OrphanGarbageCollector(
        kubernetes_client=k8s_client_facade,
        config=config,
        logger=MagicMock(),
        known_request_ids=lambda: [known_id],
        interval_seconds=9999,
    )

    orphans = await gc.run_once()

    assert orphans == [], f"Expected no orphans for a known pod; got {orphans}"
