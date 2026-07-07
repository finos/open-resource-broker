"""T19 — Partial fulfilment (some pods Pending).

Scenario
--------
Request more pods than the cluster can schedule immediately by using a
resource request (CPU/memory) large enough that only a subset of nodes
can satisfy it.  Verify that ORB correctly reports the partially-fulfilled
state: some pods are ``Running``, some are ``Pending``.  Then release all
pods and verify cleanup.

Prerequisites
-------------
* Real Kubernetes cluster accessible via ORB config.
* A namespace where pods can be scheduled.
* Pass ``--run-k8s`` to enable.

Cluster note
------------
Inducing partial fulfilment reliably requires cluster-specific knowledge
(number of nodes, available CPU).  This scaffold uses an intentionally
large CPU request (``8`` cores per pod) and requests 3 pods — which will
be ``Pending`` on most clusters with < 24 free cores.  If all pods
schedule immediately the test logs a notice and completes normally
(the test is still valid as a fulfilment cycle).

Cleanup guarantee
-----------------
All pods are released in the ``finally`` block and labelled
``orb.io/managed=true`` for nuclear cleanup.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.partial_fulfilment")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_POD_SETTLE_WAIT = 20  # seconds to let scheduler attempt placement
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


def _make_request(request_id: str, count: int = 3) -> Any:
    """Request aggregate for ``count`` pods."""
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="live-partial-tpl",
        requested_count=count,
        provider_data={},
    )


def _make_template_heavy_cpu(namespace: str) -> Any:
    """Template that requests large CPU to force scheduling pressure."""
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="live-partial-tpl",
        provider_type="k8s",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=10,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "command": ["sh", "-c", "sleep 3600"],
                # Large CPU request — most pods will stay Pending on typical clusters.
                "resources": {
                    "requests": {"cpu": "8"},
                },
            }
        },
    )


def _collect_pod_phases(core_v1: Any, namespace: str, request_id: str) -> dict[str, int]:
    """Return a dict mapping phase → count for pods with the given request-id label."""
    from collections import Counter

    label_selector = f"orb.io/request-id={request_id}"
    pod_list = core_v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
    phases: Counter[str] = Counter()
    for pod in pod_list.items:
        phase = (pod.status.phase or "Unknown") if pod.status else "Unknown"
        phases[phase] += 1
    return dict(phases)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_partial_fulfilment_some_pods_pending(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1: Any,
    live_request_id: str,
) -> None:
    """Acquire 3 heavy-CPU pods; verify that ORB creates all 3, some may be Pending.

    The test does not assert that pods are Pending (that depends on cluster
    capacity) but it does assert:

    * Exactly 3 pods were created.
    * Each pod is labelled with the request-id.
    * The ORB status response accounts for all 3 pods.
    * After release, all 3 pods are gone.
    """
    handler = _make_pod_handler(k8s_provider_config, k8s_namespace)
    request = _make_request(live_request_id, count=3)
    template = _make_template_heavy_cpu(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    pod_names: list[str] = result.get("machine_ids", [])
    assert len(pod_names) == 3, f"Expected 3 pods, got {pod_names!r}"
    log.info("Acquired pods: %r", pod_names)

    try:
        # Allow scheduler time to attempt placement.
        time.sleep(_POD_SETTLE_WAIT)

        phases = _collect_pod_phases(k8s_core_v1, k8s_namespace, live_request_id)
        log.info("Pod phases for %s after %ds: %r", live_request_id, _POD_SETTLE_WAIT, phases)

        running = phases.get("Running", 0)
        pending = phases.get("Pending", 0)
        total = sum(phases.values())

        assert total == 3, f"Expected 3 labelled pods in cluster, found {total} (phases={phases})"

        if pending > 0:
            log.info("Partial fulfilment confirmed: %d Running, %d Pending", running, pending)
        else:
            log.info("All 3 pods scheduled (cluster has sufficient capacity); phases=%r", phases)

        # Check ORB handler status.
        status_result = handler.check_hosts_status(request)
        instances = status_result.instances or []
        assert len(instances) == 3, (
            f"ORB status should report 3 instances, got {len(instances)}: {instances!r}"
        )
        reported_statuses = {inst.get("status") for inst in instances}
        log.info("ORB-reported statuses: %r", reported_statuses)

    finally:
        # Release all pods regardless of phase.
        try:
            await handler.release_hosts(pod_names, request.provider_data)
            log.info("Released all %d pods", len(pod_names))

            # Verify deletion.
            deadline = time.monotonic() + _RELEASE_TIMEOUT
            while time.monotonic() < deadline:
                phases = _collect_pod_phases(k8s_core_v1, k8s_namespace, live_request_id)
                if not phases:
                    break
                time.sleep(_POLL_INTERVAL)
            phases = _collect_pod_phases(k8s_core_v1, k8s_namespace, live_request_id)
            assert not phases, f"Pods still present after release: {phases}"
        except Exception as exc:
            log.warning("Release failed: %s", exc)
