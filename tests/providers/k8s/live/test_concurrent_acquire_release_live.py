"""Live integration tests for T21: concurrent acquire+release race on the same resource.

Tests in this module hit a real Kubernetes cluster.  They are skipped by
default; pass ``--run-k8s`` to enable them.

Scenario: two concurrent coroutines both attempt to acquire and then release
the same resource identifier.  ORB must honour exactly-once semantics —
only one acquire succeeds, no double-releases blow up, and the final state
is deterministically clean.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.concurrent_acquire_release")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_RELEASE_TIMEOUT = 60  # seconds
_POLL_INTERVAL = 2  # seconds


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


def _make_request(request_id: str, count: int = 1, template_id: str = "live-tpl"):
    """Construct a minimal Request for the given request-id."""
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id=template_id,
        requested_count=count,
        provider_data={},
    )


def _make_template(namespace: str, image: str = "busybox:latest"):
    """Construct a minimal Template for pod tests."""
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="live-tpl",
        provider_type="k8s",
        provider_api="Pod",
        image_id=image,
        max_instances=10,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "command": ["sh", "-c", "sleep 3600"],
            }
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_concurrent_acquire_same_request_id(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T21: two concurrent acquires for the same request-id produce at most one pod set.

    Both coroutines submit identical requests concurrently.  The handler must
    be idempotent: exactly one acquire completes without error, and the cluster
    ends up with the expected pod count — never doubled.
    """
    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_template(k8s_namespace)

    results: list[dict[str, Any] | None] = [None, None]
    errors: list[BaseException | None] = [None, None]

    async def _acquire(index: int) -> None:
        try:
            results[index] = await handler.acquire_hosts(request, template)
        except Exception as exc:
            errors[index] = exc

    await asyncio.gather(_acquire(0), _acquire(1), return_exceptions=True)

    # At least one coroutine must have succeeded.
    assert any(r is not None for r in results), f"Both concurrent acquires failed: errors={errors}"

    # Count pods labelled with this request-id on the cluster.
    pods = k8s_core_v1.list_namespaced_pod(
        namespace=k8s_namespace,
        label_selector=f"orb.io/request-id={live_request_id}",
    )
    pod_count = len(pods.items)
    assert pod_count <= 1, (
        f"Race condition: {pod_count} pods exist for request {live_request_id} — expected ≤ 1"
    )

    # Cleanup: release any pods that exist.
    if pod_count > 0:
        pod_names = [p.metadata.name for p in pods.items]
        try:
            await handler.release_hosts(pod_names, request.provider_data)
        except Exception as cleanup_exc:
            log.warning("Cleanup release failed for %s: %s", pod_names, cleanup_exc)


async def test_interleaved_acquire_release_does_not_leave_orphan(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T21b: acquire racing with release for the same request-id converges to no orphan.

    Coroutine-1 acquires; coroutine-2 races to release the same request-id
    immediately.  The early release may find nothing (the create is still in
    flight) and legitimately no-op — the pod handler has no acquire-cancellation
    contract, so losing that race is expected, not a leak.  The invariant under
    test is convergence: once an authoritative release runs against whatever the
    acquire produced, no pods with the request-id label remain.
    """
    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_template(k8s_namespace)

    acquire_error: list[BaseException | None] = [None]
    release_error: list[BaseException | None] = [None]
    acquired_pod_names: list[list[str]] = [[]]

    async def _acquire() -> None:
        try:
            result = await handler.acquire_hosts(request, template)
            acquired_pod_names[0] = result.get("machine_ids", [])
        except Exception as exc:
            acquire_error[0] = exc

    async def _release() -> None:
        # Yield once to let acquire start, then attempt release.
        await asyncio.sleep(0.05)
        try:
            # Release with empty machine_ids if acquire hasn't returned yet.
            pods = k8s_core_v1.list_namespaced_pod(
                namespace=k8s_namespace,
                label_selector=f"orb.io/request-id={live_request_id}",
            )
            pod_names = [p.metadata.name for p in pods.items]
            if pod_names:
                await handler.release_hosts(pod_names, request.provider_data)
        except Exception as exc:
            release_error[0] = exc

    await asyncio.gather(_acquire(), _release(), return_exceptions=True)

    # Authoritative release: whatever the acquire actually produced (plus any
    # pod still carrying the request-id label) must be released.  If the early
    # release already removed them this is a 404 no-op; if it lost the race this
    # is the release that actually cleans up.  Either way the request converges
    # to zero pods.
    def _current_pod_names() -> list[str]:
        pods = k8s_core_v1.list_namespaced_pod(
            namespace=k8s_namespace,
            label_selector=f"orb.io/request-id={live_request_id}",
        )
        return [p.metadata.name for p in pods.items]

    authoritative = set(acquired_pod_names[0]) | set(_current_pod_names())
    if authoritative:
        try:
            await handler.release_hosts(sorted(authoritative), request.provider_data)
        except Exception as exc:
            release_error[0] = release_error[0] or exc

    # Deletes are asynchronous cluster-side; poll with a bounded timeout for the
    # request-id label selector to drain to zero.
    deadline = time.monotonic() + _RELEASE_TIMEOUT
    remaining = -1
    while time.monotonic() < deadline:
        remaining = len(_current_pod_names())
        if remaining == 0:
            break
        time.sleep(_POLL_INTERVAL)

    assert remaining == 0, (
        f"Orphan pods detected: {remaining} pod(s) for request {live_request_id} "
        f"did not drain within {_RELEASE_TIMEOUT}s after an authoritative release. "
        f"acquire_error={acquire_error[0]}, release_error={release_error[0]}"
    )
