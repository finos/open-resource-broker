"""Live integration tests for T26: watch buffer overflow with >10k events.

Tests in this module hit a real Kubernetes cluster.  They are skipped by
default; pass ``--run-k8s`` to enable them.

Scenario: ORB's Kubernetes watch loop maintains an internal event buffer.
When the cluster emits more than 10 000 events in a short burst (e.g. a
mass-delete of pods), the watch consumer must not drop events, deadlock,
or crash.  After the burst the watch loop must still be responsive.

The test generates the burst by rapidly creating and deleting a large batch
of short-lived pods, then asserting that:
- The watch loop is still alive after the burst.
- No events are double-counted.
- The handler can still serve a normal acquire+release after the burst.

Note: testing a literal 10k event burst requires a dedicated load-test
cluster.  This test uses 50 pods as a representative burst that still
exercises buffer-management code paths.  Increase _BURST_POD_COUNT in
performance environments.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.watch_buffer_overflow")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_BURST_POD_COUNT = 50  # pods in the burst (representative of high-volume)
_BURST_NAMESPACE_LABEL = "orb.io/watch-overflow-test"
_BURST_TIMEOUT = 300  # seconds — bulk pod create/delete can be slow
_WATCH_SETTLE_WAIT = 15  # seconds for watch loop to process the burst
_POLL_INTERVAL = 3  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_k8s_client(k8s_provider_config: dict):
    """Build a live K8sClient from the ORB provider config."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    config = K8sProviderConfig(
        namespace=k8s_provider_config.get("namespace"),
        kubeconfig_path=k8s_provider_config.get("kubeconfig_path"),
        context=k8s_provider_config.get("context"),
        in_cluster=k8s_provider_config.get("in_cluster"),
    )
    logger = MagicMock()
    client = K8sClient(config=config, logger=logger)
    client.load_config()
    return client, config


def _make_pod_handler(k8s_provider_config: dict):
    """Construct a live K8sPodHandler."""
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

    client, config = _build_k8s_client(k8s_provider_config)
    logger = MagicMock()
    return K8sPodHandler(kubernetes_client=client, config=config, logger=logger), config


def _make_request(request_id: str, count: int = 1):
    """Construct a minimal Request."""
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="live-tpl",
        requested_count=count,
        provider_data={},
    )


def _make_template(namespace: str):
    """Build a minimal Template."""
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="live-tpl",
        provider_type="k8s",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=100,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "command": ["sh", "-c", "exit 0"],
                "restart_policy": "Never",
            }
        },
    )


def _create_burst_pod(core_v1, namespace: str, pod_index: int) -> str:
    """Create a single burst pod and return its name."""
    from kubernetes.client.models import V1Container, V1ObjectMeta, V1Pod, V1PodSpec

    pod_name = f"orb-overflow-burst-{pod_index:05d}"
    pod = V1Pod(
        metadata=V1ObjectMeta(
            name=pod_name,
            namespace=namespace,
            labels={
                _BURST_NAMESPACE_LABEL: "true",
                "orb.io/test": "watch-overflow",
            },
        ),
        spec=V1PodSpec(
            restart_policy="Never",
            containers=[
                V1Container(
                    name="burst",
                    image="busybox:latest",
                    command=["sh", "-c", "exit 0"],
                )
            ],
        ),
    )
    try:
        core_v1.create_namespaced_pod(namespace=namespace, body=pod)
    except Exception as exc:
        if getattr(exc, "status", None) == 409:
            pass  # Already exists — idempotent.
        else:
            raise
    return pod_name


def _create_managed_burst_pod(core_v1, namespace: str, request_id: str, pod_index: int) -> str:
    """Create a burst pod carrying the ORB managed + request-id labels.

    Unlike :func:`_create_burst_pod`, these pods match the watcher's
    ``orb.io/managed=true`` label selector and carry ``orb.io/request-id``
    so the real :class:`K8sWatcher` ingests them into its cache.  The
    request-id scopes them to a single test so the cache-dedup assertion is
    unaffected by any pods a concurrently-running test creates.
    """
    from kubernetes.client.models import V1Container, V1ObjectMeta, V1Pod, V1PodSpec

    pod_name = f"orb-overflow-managed-{pod_index:05d}"
    pod = V1Pod(
        metadata=V1ObjectMeta(
            name=pod_name,
            namespace=namespace,
            labels={
                _BURST_NAMESPACE_LABEL: "true",
                "orb.io/managed": "true",
                "orb.io/request-id": request_id,
                "orb.io/provider-api": "Pod",
            },
        ),
        spec=V1PodSpec(
            restart_policy="Never",
            containers=[
                V1Container(
                    name="burst",
                    image="busybox:latest",
                    command=["sh", "-c", "exit 0"],
                )
            ],
        ),
    )
    try:
        core_v1.create_namespaced_pod(namespace=namespace, body=pod)
    except Exception as exc:
        if getattr(exc, "status", None) == 409:
            pass  # Already exists — idempotent.
        else:
            raise
    return pod_name


def _delete_managed_burst_pods(core_v1, namespace: str, request_id: str) -> int:
    """Delete the managed burst pods for ``request_id``; return count deleted."""
    from kubernetes.client.models import V1DeleteOptions

    deleted = 0
    try:
        pod_list = core_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"orb.io/request-id={request_id}",
        )
        for pod in pod_list.items:
            try:
                core_v1.delete_namespaced_pod(
                    name=pod.metadata.name,
                    namespace=namespace,
                    body=V1DeleteOptions(grace_period_seconds=0),
                )
                deleted += 1
            except Exception as exc:
                if getattr(exc, "status", None) != 404:
                    log.warning("Failed to delete managed burst pod %s: %s", pod.metadata.name, exc)
    except Exception as exc:
        log.warning("delete_managed_burst_pods failed: %s", exc)
    return deleted


def _delete_burst_pods(core_v1, namespace: str) -> int:
    """Delete all burst pods and return count deleted."""
    try:
        from kubernetes.client.models import V1DeleteOptions

        pod_list = core_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"{_BURST_NAMESPACE_LABEL}=true",
        )
        deleted = 0
        for pod in pod_list.items:
            try:
                core_v1.delete_namespaced_pod(
                    name=pod.metadata.name,
                    namespace=namespace,
                    body=V1DeleteOptions(grace_period_seconds=0),
                )
                deleted += 1
            except Exception as exc:
                if getattr(exc, "status", None) != 404:
                    log.warning("Failed to delete burst pod %s: %s", pod.metadata.name, exc)
        return deleted
    except Exception as exc:
        log.warning("delete_burst_pods failed: %s", exc)
        return 0


def _wait_burst_pods_gone(core_v1, namespace: str, timeout: float = _BURST_TIMEOUT) -> int:
    """Wait until all burst pods are deleted; return remaining count."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pods = core_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"{_BURST_NAMESPACE_LABEL}=true",
        )
        remaining = len(pods.items)
        if remaining == 0:
            return 0
        time.sleep(_POLL_INTERVAL)
    return remaining


def _build_watcher(k8s_provider_config: dict, namespace: str):
    """Construct the real ORB pod watcher against the live cluster.

    Returns ``(K8sWatcher, PodStateCache)``.  The watcher consumes the
    namespace's pod event stream (ADDED/MODIFIED/DELETED) and upserts each
    event into the cache keyed by ``(request_id, pod_name)`` — the key that
    guarantees a pod is represented by exactly one cache entry regardless of
    how many events it generates.  That key-based upsert is the ORB watch
    loop's de-duplication contract: a burst of events for a pod collapses to
    a single live cache entry, so the cache size is bounded by the number of
    distinct pods, never by the number of events observed.
    """
    from orb.providers.k8s.watch.pod_state_cache import PodStateCache
    from orb.providers.k8s.watch.watcher import K8sWatcher

    client, _config = _build_k8s_client(k8s_provider_config)
    cache = PodStateCache()
    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace=namespace,
        # Short apiserver watch timeout so a session that starts before the
        # burst still recycles and picks the events up promptly.
        watch_timeout_seconds=60,
        base_backoff_seconds=0.5,
        max_backoff_seconds=2.0,
    )
    return watcher, cache


async def _wait_for(check, *, timeout: float, poll_interval: float = 1.0) -> bool:
    """Poll ``check`` until it returns True or ``timeout`` seconds elapse.

    Returns whether the condition became true within the deadline.  Used
    instead of a fixed sleep so the test exits as soon as the watcher has
    converged rather than always waiting the worst-case duration.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        await asyncio.sleep(poll_interval)
    return check()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_watch_survives_burst_event_flood(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T26a: watch loop remains responsive after a burst of rapid pod create/delete events.

    Creates _BURST_POD_COUNT pods in parallel, then bulk-deletes them,
    generating a dense event stream.  After the burst the handler must still
    be able to service a normal acquire.
    """
    # Create burst pods concurrently using threads.
    create_errors: list[Exception] = []

    def _create(idx: int) -> None:
        try:
            _create_burst_pod(k8s_core_v1, k8s_namespace, idx)
        except Exception as exc:
            create_errors.append(exc)

    threads = [threading.Thread(target=_create, args=(i,)) for i in range(_BURST_POD_COUNT)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    if create_errors:
        log.warning("Some burst pod creates failed (%d): %s", len(create_errors), create_errors[:3])

    pod_names: list[str] = []
    handler = None
    request = None
    try:
        # Brief pause so the watch stream fills up.
        time.sleep(3)

        # Bulk-delete to generate a second wave of events.
        deleted = _delete_burst_pods(k8s_core_v1, k8s_namespace)
        log.info("Deleted %d burst pods", deleted)

        remaining = _wait_burst_pods_gone(k8s_core_v1, k8s_namespace, timeout=120)
        assert remaining == 0, f"{remaining} burst pods still present after bulk delete"

        time.sleep(_WATCH_SETTLE_WAIT)

        # After the burst, a normal acquire+release must succeed.
        handler, _ = _make_pod_handler(k8s_provider_config)
        request = _make_request(live_request_id, count=1)
        template = _make_template(k8s_namespace)

        result = await handler.acquire_hosts(request, template)
        assert result is not None, (
            "acquire_hosts returned None after watch burst — handler may be in a broken state"
        )
        pod_names = result.get("machine_ids", [])
    finally:
        # Cleanup is unconditional so a failing assert does not orphan
        # burst pods or the post-burst acquire pod.
        try:
            _delete_burst_pods(k8s_core_v1, k8s_namespace)
        except Exception as exc:
            log.warning("Burst-pod finally cleanup failed: %s", exc)
        if pod_names and handler is not None and request is not None:
            try:
                await handler.release_hosts(pod_names, request.provider_data)
            except Exception as exc:
                log.warning("Post-burst cleanup release failed: %s", exc)


async def test_watch_loop_no_duplicate_events_after_overflow(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T26b: the watch loop does not double-count events during an event burst.

    Runs the real ORB :class:`K8sWatcher` against the live cluster, then
    creates and deletes a burst of ORB-labelled pods to drive a dense
    ADDED→MODIFIED→DELETED event stream through the watcher's ingest path.

    The de-duplication contract: the watcher upserts every event into a
    :class:`PodStateCache` keyed by ``(request_id, pod_name)``.  However
    many events a pod produces, the cache holds at most one live entry for
    it — so the number of distinct live cache entries can never exceed the
    number of pods actually created, and after the pods are deleted the
    watcher must converge to zero live entries for the request.  A double-
    counting watch loop would leave the cache over-populated (more entries
    than pods) or fail to converge after the DELETE wave.
    """
    watcher, cache = _build_watcher(k8s_provider_config, k8s_namespace)
    burst_count = 20

    watcher.start()
    try:
        # Give the watch session a moment to open before the burst so the
        # ADDED events are delivered over the stream rather than only via a
        # later re-LIST.
        await asyncio.sleep(2)

        for i in range(burst_count):
            _create_managed_burst_pod(k8s_core_v1, k8s_namespace, live_request_id, i + 1000)

        # Wait until the watcher has ingested every created pod.  The cache
        # entry count for this request must equal the pod count exactly —
        # not a multiple of it — which is the no-double-count assertion.
        added_ok = await _wait_for(
            lambda: (
                len([s for s in (cache.get(live_request_id) or []) if not s.deleted]) == burst_count
            ),
            timeout=90,
        )
        live_entries = [s for s in (cache.get(live_request_id) or []) if not s.deleted]
        assert added_ok, (
            f"Watcher did not converge to {burst_count} distinct live cache entries; "
            f"saw {len(live_entries)} (double-counting would exceed {burst_count})"
        )
        assert len(live_entries) == burst_count, (
            f"Cache holds {len(live_entries)} live entries for {burst_count} pods — "
            "watch loop double-counted events into duplicate cache entries."
        )

        # Delete the whole burst to drive the DELETE wave through the stream.
        _delete_managed_burst_pods(k8s_core_v1, k8s_namespace, live_request_id)

        # After the DELETE events the watcher must converge to zero live
        # entries for the request — proving DELETE events are not dropped or
        # double-processed into stuck entries.
        drained_ok = await _wait_for(
            lambda: len([s for s in (cache.get(live_request_id) or []) if not s.deleted]) == 0,
            timeout=90,
        )
        remaining = [s for s in (cache.get(live_request_id) or []) if not s.deleted]
        assert drained_ok, (
            f"Watcher did not drain live cache entries after burst delete; "
            f"{len(remaining)} still present"
        )

        # The watch task must still be alive and healthy after the burst.
        assert watcher.is_running(), "Watch loop task died after event burst"
    finally:
        await watcher.stop()
        _delete_managed_burst_pods(k8s_core_v1, k8s_namespace, live_request_id)


async def test_normal_operation_after_10k_event_simulation(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T26c: full acquire→release cycle succeeds after a simulated high-volume event burst.

    Simulates high event volume by rapidly cycling through pods twice
    (double-burst), then asserts the handler can complete a clean
    acquire+release for a new request.  Validates that the watch buffer does
    not enter a permanently broken state.
    """
    for _round in range(2):
        batch = min(_BURST_POD_COUNT, 30)
        threads = [
            threading.Thread(
                target=_create_burst_pod,
                args=(k8s_core_v1, k8s_namespace, i + _round * 200),
            )
            for i in range(batch)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        time.sleep(2)
        _delete_burst_pods(k8s_core_v1, k8s_namespace)
        _wait_burst_pods_gone(k8s_core_v1, k8s_namespace, timeout=60)
        time.sleep(3)

    time.sleep(_WATCH_SETTLE_WAIT)

    # Full acquire → release cycle.
    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_template(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    assert result is not None, "acquire_hosts returned None after double burst"
    pod_names = result.get("machine_ids", [])

    # Wait for the pod to settle.
    time.sleep(10)

    pods = k8s_core_v1.list_namespaced_pod(
        namespace=k8s_namespace,
        label_selector=f"orb.io/request-id={live_request_id}",
    )
    assert pods.items, "No pod found after acquire following double burst"

    if pod_names:
        await handler.release_hosts(pod_names, request.provider_data)

    # Verify cleanup.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        pods = k8s_core_v1.list_namespaced_pod(
            namespace=k8s_namespace,
            label_selector=f"orb.io/request-id={live_request_id}",
        )
        if not pods.items:
            break
        time.sleep(_POLL_INTERVAL)

    pods = k8s_core_v1.list_namespaced_pod(
        namespace=k8s_namespace,
        label_selector=f"orb.io/request-id={live_request_id}",
    )
    assert not pods.items, f"{len(pods.items)} pod(s) remain after release following double burst"
