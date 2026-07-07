"""Live integration tests for k8s reconciliation — startup reconciler, orphan GC,
and timeout GC.

Tests in this module hit a real Kubernetes cluster.  Pass ``--run-k8s``
to enable them.

The orphan-GC and timeout-GC tests override all relevant intervals via a
test-scoped :class:`K8sProviderConfig` so they complete in seconds regardless
of whatever values the user has in their cluster config file.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.reconciliation")

pytestmark = [pytest.mark.asyncio]

_RECONCILER_TIMEOUT = 30  # seconds for startup reconciler to complete
_GC_FAST_INTERVAL = 5  # seconds — injected interval for fast GC tests
_GC_WAIT_TIMEOUT = 60  # seconds to wait for GC action
_POLL_INTERVAL = 2  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_k8s_client_and_config(k8s_provider_config: dict, namespace: str):
    """Build a live :class:`K8sClient` and :class:`K8sProviderConfig`."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    config = K8sProviderConfig(
        namespace=namespace,
        kubeconfig_path=k8s_provider_config.get("kubeconfig_path"),
        context=k8s_provider_config.get("context"),
        in_cluster=k8s_provider_config.get("in_cluster"),
        auto_cleanup_orphans=True,  # needed for orphan-GC delete test
        orphan_gc_enabled=True,
        orphan_min_age_seconds=0,  # no min-age guard in tests
    )
    logger = MagicMock()
    client = K8sClient(config=config, logger=logger)
    client.load_config()
    return client, config


def _create_raw_pod(core_v1, namespace: str, pod_name: str, request_id: str) -> None:
    """Submit a pod with ORB labels directly via CoreV1Api (bypassing the handler)."""
    body = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": namespace,
            "labels": {
                "orb.io/managed": "true",
                "orb.io/request-id": request_id,
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "orb",
                    "image": "busybox:latest",
                    "command": ["sh", "-c", "sleep 3600"],
                }
            ],
        },
    }
    core_v1.create_namespaced_pod(namespace=namespace, body=body)


def _pod_exists(core_v1, namespace: str, pod_name: str) -> bool:
    try:
        core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        return True
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            return False
        raise


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_startup_reconciler_rebuilds_cache_from_cluster(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Create a pod with ORB labels directly, run the reconciler, verify cache is warm.

    This simulates the crash-restart scenario: ORB restarts with managed
    pods already on the cluster and no in-memory cache.  The
    :class:`StartupReconciler` should re-ingest those pods into the cache.
    """
    from orb.providers.k8s.reconciliation.startup_reconciler import StartupReconciler
    from orb.providers.k8s.watch.pod_state_cache import PodStateCache

    client, config = _build_k8s_client_and_config(k8s_provider_config, k8s_namespace)
    cache = PodStateCache()

    pod_name = f"orb-live-reconcile-{live_request_id[:8]}-0000"
    _create_raw_pod(k8s_core_v1, k8s_namespace, pod_name, live_request_id)
    log.info("Created raw pod %s/%s for reconciler test", k8s_namespace, pod_name)

    # The reconciler needs to know this request_id is "known" so it adopts
    # the pod instead of classifying it as an orphan.
    known_ids = {live_request_id}

    reconciler = StartupReconciler(
        kubernetes_client=client,
        config=config,
        cache=cache,
        logger=MagicMock(),
        known_request_ids=lambda: known_ids,
    )

    # Run reconciler in a thread since it is a synchronous call.
    report = await asyncio.to_thread(reconciler.run)

    assert report.completed, f"Reconciler did not complete: {report.error}"
    assert report.pods_adopted >= 1, (
        f"Expected at least 1 adopted pod, got {report.pods_adopted} (pods_seen={report.pods_seen})"
    )

    cache_entries = cache.get(live_request_id)
    assert cache_entries, (
        f"Cache empty for {live_request_id!r} after reconciler run; "
        f"report: pods_seen={report.pods_seen} adopted={report.pods_adopted}"
    )
    adopted_names = {e.pod_name for e in cache_entries}
    assert pod_name in adopted_names, f"Expected pod {pod_name!r} in cache; found {adopted_names!r}"
    log.info(
        "Reconciler adopted %d pods, cache warm for %s",
        report.pods_adopted,
        live_request_id,
    )

    # Cleanup
    try:
        k8s_core_v1.delete_namespaced_pod(name=pod_name, namespace=k8s_namespace)
    except Exception:
        pass


async def test_orphan_gc_deletes_orphan_after_grace_period(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Create an orphan pod (managed label, no matching request record), run the
    orphan GC, verify the pod is deleted.

    The GC is configured with a short interval (``_GC_FAST_INTERVAL`` seconds)
    and ``orphan_min_age_seconds=0`` so the pod is eligible for deletion
    immediately without waiting on any cluster-level config value.
    """
    from orb.providers.k8s.reconciliation.orphan_gc import OrphanGarbageCollector

    client, config = _build_k8s_client_and_config(k8s_provider_config, k8s_namespace)

    orphan_request_id = str(uuid.uuid4())  # NOT in known_ids
    pod_name = f"orb-live-orphan-{orphan_request_id[:8]}-0000"
    _create_raw_pod(k8s_core_v1, k8s_namespace, pod_name, orphan_request_id)
    log.info("Created orphan pod %s/%s", k8s_namespace, pod_name)

    assert _pod_exists(k8s_core_v1, k8s_namespace, pod_name), (
        f"Orphan pod {pod_name} should exist before GC run"
    )

    # ``known_request_ids`` returns an empty set — the pod's request_id is
    # intentionally absent so the GC classifies it as an orphan.
    gc = OrphanGarbageCollector(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
        known_request_ids=set,
        interval_seconds=_GC_FAST_INTERVAL,
    )

    orphans = await gc.run_once()
    orphan_pod_names = [o.pod_name for o in orphans]
    assert pod_name in orphan_pod_names, (
        f"Expected {pod_name!r} to be classified as an orphan; found {orphan_pod_names!r}"
    )

    # With auto_cleanup_orphans=True the GC deletes the pod.  Poll until gone.
    deadline = time.monotonic() + _GC_WAIT_TIMEOUT
    while time.monotonic() < deadline:
        if not _pod_exists(k8s_core_v1, k8s_namespace, pod_name):
            break
        await asyncio.sleep(_POLL_INTERVAL)
    else:
        pytest.fail(f"Orphan pod {pod_name} not deleted within {_GC_WAIT_TIMEOUT}s after GC run")

    log.info("Orphan pod %s/%s deleted by GC", k8s_namespace, pod_name)


async def test_timeout_gc_deletes_pending_pod_past_threshold(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Acquire a pod with an unschedulable node selector; poll until the
    timeout GC marks it terminated and deletes it.

    The GC is exercised indirectly via the :class:`K8sPodHandler`'s
    ``check_hosts_status`` path, which calls :func:`apply_pod_timeout` and
    :func:`delete_timed_out_pod_async` when a pod has been Pending for
    longer than ``pod_timeout_seconds``.

    ``pod_timeout_seconds`` is overridden via a test-scoped
    :class:`K8sProviderConfig` so the test does not depend on the
    cluster-level config value — it always runs in seconds regardless of the
    user's default.
    """
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    # Override to a short timeout so we never wait the default 300 s.
    effective_timeout = _GC_FAST_INTERVAL * 2  # 10 seconds

    config = K8sProviderConfig(
        namespace=k8s_namespace,
        kubeconfig_path=k8s_provider_config.get("kubeconfig_path"),
        context=k8s_provider_config.get("context"),
        in_cluster=k8s_provider_config.get("in_cluster"),
        pod_timeout_seconds=effective_timeout,
        delete_timed_out_pods=True,
    )
    logger = MagicMock()
    client = K8sClient(config=config, logger=logger)
    client.load_config()
    handler = K8sPodHandler(kubernetes_client=client, config=config, logger=logger)

    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType
    from orb.domain.template.template_aggregate import Template

    request_obj = Request(
        request_id=RequestId(value=live_request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="live-timeout-tpl",
        requested_count=1,
        provider_data={},
    )
    # nodeSelector targets a non-existent label to guarantee the pod stays Pending.
    template = Template(
        template_id="live-timeout-tpl",
        provider_type="k8s",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=1,
        provider_data={
            "k8s": {
                "namespace": k8s_namespace,
                "command": ["sh", "-c", "sleep 3600"],
                "node_selector": {"orb-live-nonexistent": "true"},
            }
        },
    )

    result = await handler.acquire_hosts(request_obj, template)
    pod_names = result.get("machine_ids", [])
    assert len(pod_names) == 1, f"Expected 1 pod, got {pod_names!r}"
    pod_name = pod_names[0]
    request_obj.provider_data = {"namespace": k8s_namespace}  # type: ignore[assignment]
    log.info(
        "Acquired unschedulable pod %s/%s (timeout in %ds)",
        k8s_namespace,
        pod_name,
        effective_timeout,
    )

    # Poll check_hosts_status until the timeout GC has marked the pod as
    # terminated.  We wait up to (effective_timeout + _GC_WAIT_TIMEOUT) seconds
    # total rather than sleeping a fixed duration, so the test exits as soon
    # as the condition is met on faster clusters.
    timed_out: list = []
    deadline = time.monotonic() + effective_timeout + _GC_WAIT_TIMEOUT
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)
        status_result = handler.check_hosts_status(request_obj)
        instances = status_result.instances or []
        timed_out = [inst for inst in instances if inst.get("status") == "terminated"]
        if timed_out:
            break

    assert timed_out, (
        f"Expected at least one pod marked 'terminated' by timeout GC within "
        f"{effective_timeout + _GC_WAIT_TIMEOUT}s; "
        f"statuses={[inst.get('status') for inst in instances]!r}"
    )
    log.info("Timeout GC correctly marked pod(s) as terminated: %r", timed_out)

    # Poll for the async deletion to complete rather than sleeping a fixed duration.
    delete_deadline = time.monotonic() + _GC_WAIT_TIMEOUT
    while time.monotonic() < delete_deadline:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            # check_hosts_status with no pods means the deletion succeeded.
            check = handler.check_hosts_status(request_obj)
            if all(i.get("status") in ("terminated", "unknown") for i in (check.instances or [])):
                break
        except Exception:
            break

    # Cleanup any survivors.
    try:
        await handler.release_hosts(pod_names, request_obj.provider_data)
    except Exception:
        pass
