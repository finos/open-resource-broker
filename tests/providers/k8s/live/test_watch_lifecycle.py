"""Live integration tests for the :class:`K8sWatcher` watch loop.

Tests in this module hit a real Kubernetes cluster.  Pass ``--run-k8s``
to enable them.

The watcher is an asyncio background task that streams pod events from
the apiserver and populates a :class:`PodStateCache`.  These tests
verify that the cache is populated correctly when pods are created and
deleted.

The 410 Gone reconnect test cannot be reproduced reliably by injecting
into the real apiserver, so it is marked ``skip`` with an explanatory
reason pointing to the unit-test coverage.
"""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.watch")

pytestmark = [pytest.mark.asyncio]

_CACHE_POPULATE_TIMEOUT = 60  # seconds to wait for cache to register a pod event
_POLL_INTERVAL = 1  # seconds
_POD_CREATE_TIMEOUT = 30  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_watcher_and_cache(k8s_provider_config: dict, namespace: str):
    """Construct a live :class:`K8sWatcher` and its :class:`PodStateCache`."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient
    from orb.providers.k8s.watch.pod_state_cache import PodStateCache
    from orb.providers.k8s.watch.watcher import K8sWatcher

    config = K8sProviderConfig(
        namespace=namespace,
        kubeconfig_path=k8s_provider_config.get("kubeconfig_path"),
        context=k8s_provider_config.get("context"),
        in_cluster=k8s_provider_config.get("in_cluster"),
    )
    logger = MagicMock()
    client = K8sClient(config=config, logger=logger)
    client.load_config()

    cache = PodStateCache()
    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=logger,
        namespace=namespace,
        # Use a short watch timeout so the stream reconnects quickly in tests.
        watch_timeout_seconds=30,
    )
    return watcher, cache, client, config


def _build_pod_body(namespace: str, pod_name: str, request_id: str, image: str = "busybox:latest"):
    """Build a minimal pod body dict for direct API submission."""
    return {
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
                    "image": image,
                    "command": ["sh", "-c", "sleep 3600"],
                }
            ],
        },
    }


async def _wait_for_cache_entry(
    cache,
    request_id: str,
    timeout: float = _CACHE_POPULATE_TIMEOUT,
) -> list:
    """Poll until the cache has at least one entry for ``request_id``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entries = cache.get(request_id)
        if entries:
            return entries
        await asyncio.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"Cache never populated for request_id={request_id!r} within {timeout}s")


async def _wait_for_cache_empty(
    cache,
    request_id: str,
    timeout: float = _CACHE_POPULATE_TIMEOUT,
) -> None:
    """Poll until the cache has no entries for ``request_id``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entries = cache.get(request_id)
        if entries is None or len(entries) == 0:
            return
        await asyncio.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"Cache not cleared for request_id={request_id!r} within {timeout}s")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_watch_receives_pod_create_event(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Start watcher, create a pod via raw API, verify it appears in the cache."""
    watcher, cache, _client, _config = _build_watcher_and_cache(k8s_provider_config, k8s_namespace)

    watcher.start()
    try:
        # Give the watcher a moment to connect and establish the watch stream.
        await asyncio.sleep(2)

        pod_name = f"orb-live-watch-{live_request_id[:8]}-0000"
        body = _build_pod_body(k8s_namespace, pod_name, live_request_id)
        k8s_core_v1.create_namespaced_pod(namespace=k8s_namespace, body=body)
        log.info("Created pod %s/%s for watch test", k8s_namespace, pod_name)

        # Wait until the watcher populates the cache.
        entries = await _wait_for_cache_entry(cache, live_request_id)
        assert any(e.pod_name == pod_name for e in entries), (
            f"Expected pod {pod_name} in cache, got: {[e.pod_name for e in entries]!r}"
        )
        log.info("Cache populated for %s: %r", live_request_id, [e.pod_name for e in entries])

    finally:
        await watcher.stop()
        # Best-effort pod cleanup (nuclear cleanup will also cover this).
        try:
            k8s_core_v1.delete_namespaced_pod(
                name=f"orb-live-watch-{live_request_id[:8]}-0000",
                namespace=k8s_namespace,
            )
        except Exception:
            pass


async def test_watch_receives_pod_delete_event(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Create pod, wait for cache entry, delete pod, verify cache entry is removed."""
    watcher, cache, _client, _config = _build_watcher_and_cache(k8s_provider_config, k8s_namespace)

    watcher.start()
    try:
        await asyncio.sleep(2)

        pod_name = f"orb-live-watch-del-{live_request_id[:8]}-0000"
        body = _build_pod_body(k8s_namespace, pod_name, live_request_id)
        k8s_core_v1.create_namespaced_pod(namespace=k8s_namespace, body=body)
        log.info("Created pod %s/%s for delete-event test", k8s_namespace, pod_name)

        # Wait for the cache to register the pod.
        await _wait_for_cache_entry(cache, live_request_id)

        # Delete the pod.
        k8s_core_v1.delete_namespaced_pod(name=pod_name, namespace=k8s_namespace)
        log.info("Deleted pod %s/%s", k8s_namespace, pod_name)

        # Wait until the cache clears the entry.
        await _wait_for_cache_empty(cache, live_request_id)
        entries = cache.get(live_request_id)
        assert not entries, (
            f"Expected empty cache for {live_request_id!r} after pod deletion, got: {entries!r}"
        )

    finally:
        await watcher.stop()
        try:
            k8s_core_v1.delete_namespaced_pod(
                name=f"orb-live-watch-del-{live_request_id[:8]}-0000",
                namespace=k8s_namespace,
            )
        except Exception:
            pass


@pytest.mark.skip(
    reason=(
        "410 Gone reconnect requires apiserver-side event ejection (resource version "
        "compaction) which cannot be triggered reliably from a test client.  "
        "The reconnect path is covered by unit tests in "
        "tests/providers/k8s/unit/watch/test_watcher.py."
    )
)
async def test_watch_survives_410_gone_reconnect(
    k8s_provider_config: dict,
    k8s_namespace: str,
) -> None:
    """410 Gone reconnect — covered by unit tests; skipped here."""
    pass
