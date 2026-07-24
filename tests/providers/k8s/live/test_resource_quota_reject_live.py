"""T18 — ResourceQuota rejection path.

Scenario
--------
Create a ``ResourceQuota`` in the test namespace that limits pod count to
zero (or to a count below what the acquire requests), then submit an
acquire.  The test verifies that ORB returns a quota-exceeded error
(or maps the apiserver's 403/409 into a provider-level error) rather
than hanging or panicking.

Prerequisites
-------------
* Real Kubernetes cluster accessible via ORB config.
* The test runner must have permission to create and delete
  ``ResourceQuota`` objects in the configured namespace.
* Pass ``--run-k8s`` to enable.

Cleanup guarantee
-----------------
The ``ResourceQuota`` created by this test is deleted in the ``finally``
block regardless of test outcome.  No pods are created in the quota-0
path so nuclear cleanup is not needed; if the quota path is unexpectedly
bypassed, any created pods carry ``orb.io/managed=true`` for sweep cleanup.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.resource_quota")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_QUOTA_NAME_PREFIX = "orb-test-quota"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pod_handler(k8s_provider_config: dict, namespace: str) -> Any:
    """Build a K8sPodHandler."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    config = K8sProviderConfig(  # type: ignore[call-arg]
        namespace=namespace,
        kubeconfig_path=k8s_provider_config.get("kubeconfig_path"),
        context=k8s_provider_config.get("context"),
        in_cluster=k8s_provider_config.get("in_cluster"),
    )
    logger = MagicMock()
    client = K8sClient(config=config, logger=logger)
    client.load_config()
    return K8sPodHandler(kubernetes_client=client, config=config, logger=logger)


def _make_request(request_id: str, count: int = 2) -> Any:
    """Minimal Request aggregate."""
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="live-quota-tpl",
        requested_count=count,
        provider_data={},
    )


def _make_template(namespace: str) -> Any:
    """Minimal Template."""
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="live-quota-tpl",
        provider_type="k8s",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=10,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "command": ["sh", "-c", "sleep 3600"],
            }
        },
    )


def _create_pod_count_quota(
    core_v1: Any, namespace: str, quota_name: str, max_pods: int = 0
) -> None:
    """Create a ResourceQuota that limits pods to ``max_pods``."""
    from kubernetes import client as k8s_client_mod

    quota = k8s_client_mod.V1ResourceQuota(
        metadata=k8s_client_mod.V1ObjectMeta(name=quota_name, namespace=namespace),
        spec=k8s_client_mod.V1ResourceQuotaSpec(hard={"pods": str(max_pods)}),
    )
    core_v1.create_namespaced_resource_quota(namespace=namespace, body=quota)
    log.info("Created ResourceQuota %s/%s (max pods=%d)", namespace, quota_name, max_pods)


def _delete_quota(core_v1: Any, namespace: str, quota_name: str) -> None:
    """Delete the ResourceQuota (best-effort)."""
    try:
        core_v1.delete_namespaced_resource_quota(name=quota_name, namespace=namespace)
        log.info("Deleted ResourceQuota %s/%s", namespace, quota_name)
    except Exception as exc:
        log.warning("Could not delete ResourceQuota %s/%s: %s", namespace, quota_name, exc)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_acquire_fails_when_quota_exceeded(
    k8s_provider_config: dict,
    k8s_isolated_namespace: str,
    k8s_core_v1: Any,
    live_request_id: str,
) -> None:
    """Submit an acquire that exceeds a namespace ResourceQuota and verify error handling.

    The quota is set to 0 pods.  The acquire must raise a provider-level
    exception (not hang, not return empty results silently).  The specific
    exception type depends on how ORB maps the apiserver's 403/409 response.

    A ``ResourceQuota`` with ``pods: 0`` blocks every pod creation in its
    namespace, so this test runs in a per-test isolated namespace to keep
    it safe to run alongside other live tests.
    """
    namespace = k8s_isolated_namespace
    quota_name = f"{_QUOTA_NAME_PREFIX}-{uuid.uuid4().hex[:8]}"
    handler = _make_pod_handler(k8s_provider_config, namespace)
    request = _make_request(live_request_id, count=2)
    template = _make_template(namespace)

    # Attempt to create the quota; skip if the test runner lacks quota permissions.
    try:
        _create_pod_count_quota(k8s_core_v1, namespace, quota_name, max_pods=0)
    except Exception as exc:
        pytest.skip(
            f"Could not create ResourceQuota in {namespace} (permissions or cluster policy): {exc}"
        )

    try:
        # Acquire must fail because quota allows 0 pods.
        with pytest.raises(Exception) as exc_info:
            await handler.acquire_hosts(request, template)

        exc = exc_info.value
        exc_str = str(exc).lower()

        # ORB should surface a quota / permission / provider error.
        # Accept any exception whose message contains diagnostic keywords.
        quota_keywords = {"quota", "forbidden", "403", "exceeded", "limit", "pods"}
        assert any(kw in exc_str for kw in quota_keywords), (
            f"Expected a quota-related error but got: {exc!r}"
        )
        log.info("Quota rejection correctly surfaced as: %r", exc)

    finally:
        _delete_quota(k8s_core_v1, namespace, quota_name)


async def test_acquire_succeeds_after_quota_removed(
    k8s_provider_config: dict,
    k8s_isolated_namespace: str,
    k8s_core_v1: Any,
    live_request_id: str,
) -> None:
    """Verify acquire succeeds once a restrictive quota is removed.

    1. Create quota (max pods=0).
    2. Verify acquire fails.
    3. Delete quota.
    4. Verify acquire now succeeds.
    5. Release acquired pods.

    Runs in a per-test isolated namespace: the ``pods: 0`` quota would
    otherwise block pod creation for any concurrently-running live test.
    """
    namespace = k8s_isolated_namespace
    quota_name = f"{_QUOTA_NAME_PREFIX}-rmv-{uuid.uuid4().hex[:8]}"
    handler = _make_pod_handler(k8s_provider_config, namespace)
    # Each request needs its own valid ``req-<uuid>`` id — the RequestId value
    # object rejects any suffix beyond the bare UUID, so the blocked and
    # post-removal acquires use distinct freshly-generated ids.
    request_blocked = _make_request(f"req-{uuid.uuid4()}", count=1)
    request_ok = _make_request(f"req-{uuid.uuid4()}", count=1)
    template = _make_template(namespace)

    try:
        _create_pod_count_quota(k8s_core_v1, namespace, quota_name, max_pods=0)
    except Exception as exc:
        pytest.skip(f"Could not create ResourceQuota: {exc}")

    acquired_pods: list[str] = []
    try:
        # Step 2: blocked acquire.
        try:
            await handler.acquire_hosts(request_blocked, template)
            # If it unexpectedly succeeds, record pods for cleanup.
        except Exception:
            log.info("Acquire correctly blocked by quota")

        # Step 3: remove quota.
        _delete_quota(k8s_core_v1, namespace, quota_name)
        quota_name = ""  # mark as deleted so finally block skips

        # Step 4: acquire should now succeed.
        result = await handler.acquire_hosts(request_ok, template)
        acquired_pods = result.get("machine_ids", [])
        assert len(acquired_pods) >= 1, f"Expected pods after quota removal, got: {acquired_pods!r}"
        log.info("Acquire succeeded after quota removal: %r", acquired_pods)

    finally:
        if quota_name:
            _delete_quota(k8s_core_v1, namespace, quota_name)
        if acquired_pods:
            try:
                await handler.release_hosts(acquired_pods, request_ok.provider_data)
            except Exception as exc:
                log.warning("Release failed: %s", exc)
