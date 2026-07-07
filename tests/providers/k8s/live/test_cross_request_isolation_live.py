"""T20 — Cross-request label-selector isolation.

Scenario
--------
Two concurrent requests are submitted in the same namespace.  The test
verifies that each request's pods are labelled only with their own
request-id and that the ORB status query for each request returns only
that request's pods (no cross-contamination of label selectors).

Prerequisites
-------------
* Real Kubernetes cluster accessible via ORB config.
* Namespace writable by the configured service account.
* Pass ``--run-k8s`` to enable.

Cleanup guarantee
-----------------
Both requests' pods are released in the ``finally`` block.  Pods carry
``orb.io/managed=true`` for nuclear cleanup.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.cross_request_isolation")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_POD_READY_TIMEOUT = 120  # seconds
_POLL_INTERVAL = 3  # seconds
_RELEASE_TIMEOUT = 60  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pod_handler(k8s_provider_config: dict, namespace: str) -> Any:
    """Build a K8sPodHandler wired to the live cluster."""
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
    """Request aggregate."""
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="live-iso-tpl",
        requested_count=count,
        provider_data={},
    )


def _make_template(namespace: str) -> Any:
    """Template for isolation tests."""
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="live-iso-tpl",
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


def _pods_for_request(core_v1: Any, namespace: str, request_id: str) -> list[str]:
    """Return pod names labelled with ``request_id``."""
    label_selector = f"orb.io/request-id={request_id}"
    pod_list = core_v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
    return [p.metadata.name for p in pod_list.items]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_two_concurrent_requests_are_isolated(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1: Any,
    live_request_id: str,
    request_id_prefix: str,
) -> None:
    """Concurrently acquire two requests; verify pod-set isolation by label selector.

    Assertions:
    * Request A's pods have ONLY ``orb.io/request-id=<id_a>``.
    * Request B's pods have ONLY ``orb.io/request-id=<id_b>``.
    * Listing by request-A selector returns exactly ``count_a`` pods.
    * Listing by request-B selector returns exactly ``count_b`` pods.
    * No pod appears in both selectors.
    * ORB ``check_hosts_status`` for A never returns pods from B, and vice versa.
    """
    id_a = live_request_id
    id_b = f"{request_id_prefix}iso-b-{live_request_id[-8:]}"

    handler = _make_pod_handler(k8s_provider_config, k8s_namespace)
    req_a = _make_request(id_a, count=2)
    req_b = _make_request(id_b, count=2)
    template = _make_template(k8s_namespace)

    pods_a: list[str] = []
    pods_b: list[str] = []

    try:
        # Submit both acquires concurrently.
        result_a, result_b = await asyncio.gather(
            handler.acquire_hosts(req_a, template),
            handler.acquire_hosts(req_b, template),
        )
        pods_a = result_a.get("machine_ids", [])
        pods_b = result_b.get("machine_ids", [])

        assert len(pods_a) == 2, f"Request A: expected 2 pods, got {pods_a!r}"
        assert len(pods_b) == 2, f"Request B: expected 2 pods, got {pods_b!r}"
        log.info("Request A pods: %r", pods_a)
        log.info("Request B pods: %r", pods_b)

        # Allow labels to propagate.
        time.sleep(2)

        # Cluster-side label verification.
        cluster_pods_a = set(_pods_for_request(k8s_core_v1, k8s_namespace, id_a))
        cluster_pods_b = set(_pods_for_request(k8s_core_v1, k8s_namespace, id_b))

        assert cluster_pods_a == set(pods_a), (
            f"Cluster label selector for request A returned unexpected pods. "
            f"Expected {set(pods_a)}, got {cluster_pods_a}"
        )
        assert cluster_pods_b == set(pods_b), (
            f"Cluster label selector for request B returned unexpected pods. "
            f"Expected {set(pods_b)}, got {cluster_pods_b}"
        )

        cross = cluster_pods_a & cluster_pods_b
        assert not cross, (
            f"Cross-request contamination detected: pods appear in both label selectors: {cross}"
        )
        log.info("Label-selector isolation verified: no cross-contamination")

        # ORB-level status isolation.
        status_a = handler.check_hosts_status(req_a)
        status_b = handler.check_hosts_status(req_b)

        ids_a_from_orb = {inst.get("machine_id") for inst in (status_a.instances or [])}
        ids_b_from_orb = {inst.get("machine_id") for inst in (status_b.instances or [])}

        orb_cross = ids_a_from_orb & ids_b_from_orb
        assert not orb_cross, (
            f"ORB status cross-contamination: machine_ids in both A and B: {orb_cross}"
        )
        log.info("ORB status isolation verified")

    finally:
        release_errors: list[str] = []
        for pod_list, req in [(pods_a, req_a), (pods_b, req_b)]:
            if pod_list:
                try:
                    await handler.release_hosts(pod_list, req.provider_data)
                except Exception as exc:
                    release_errors.append(str(exc))
        if release_errors:
            log.warning("Release errors: %s", release_errors)
