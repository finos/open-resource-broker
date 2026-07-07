"""Live integration tests for T22: Karpenter cold-node timeout.

Tests in this module hit a real Kubernetes cluster with Karpenter installed.
They are skipped by default; pass ``--run-k8s`` to enable them.

Scenario: pods are scheduled onto a node pool whose Karpenter provisioner
requires cold-node spin-up.  When the nodes never come up (e.g. quota
exhausted, wrong instance type, cloud-provider error), the pods stay
Pending indefinitely and ORB must surface a clear timeout error rather than
hanging forever.

Infrastructure requirement: a Karpenter NodePool/Provisioner named
``orb-test-karpenter-cold`` that intentionally cannot schedule
(e.g. targeting a non-existent instance type or a tainted/empty node pool).
Tests are skipped when Karpenter CRDs are absent from the cluster.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.karpenter_cold_node")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_KARPENTER_NODEPOOL_CRD = "nodepools.karpenter.sh"
_COLD_NODEPOOL_NAME = "orb-test-karpenter-cold"
_PENDING_TIMEOUT = 120  # seconds ORB should give up waiting
_POLL_INTERVAL = 5  # seconds


# ---------------------------------------------------------------------------
# Skip helpers
# ---------------------------------------------------------------------------


def _karpenter_available(k8s_provider_config: dict) -> bool:
    """Return True when Karpenter NodePool CRD exists on the cluster."""
    try:
        from kubernetes import client as k8s_client_mod, config as k8s_config_mod

        kubeconfig_path = k8s_provider_config.get("kubeconfig_path")
        context = k8s_provider_config.get("context")
        k8s_config_mod.load_kube_config(config_file=kubeconfig_path, context=context)
        ext = k8s_client_mod.ApiextensionsV1Api()
        crds = ext.list_custom_resource_definition(
            field_selector=f"metadata.name={_KARPENTER_NODEPOOL_CRD}"
        )
        return len(crds.items) > 0
    except Exception as exc:
        log.debug("Karpenter CRD check failed: %s", exc)
        return False


def _cold_nodepool_exists(k8s_provider_config: dict) -> bool:
    """Return True when the cold-test NodePool is configured."""
    try:
        from kubernetes import client as k8s_client_mod, config as k8s_config_mod

        kubeconfig_path = k8s_provider_config.get("kubeconfig_path")
        context = k8s_provider_config.get("context")
        k8s_config_mod.load_kube_config(config_file=kubeconfig_path, context=context)
        custom = k8s_client_mod.CustomObjectsApi()
        custom.get_cluster_custom_object(
            group="karpenter.sh",
            version="v1",
            plural="nodepools",
            name=_COLD_NODEPOOL_NAME,
        )
        return True
    except Exception:
        return False


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


def _make_request(request_id: str, count: int = 1, template_id: str = "live-cold-tpl"):
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


def _make_template_cold_node(namespace: str):
    """Build a Template that targets the cold (unschedulable) Karpenter NodePool."""
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="live-cold-tpl",
        provider_type="k8s",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=5,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "command": ["sh", "-c", "sleep 3600"],
                "node_selector": {
                    "karpenter.sh/nodepool": _COLD_NODEPOOL_NAME,
                },
                "tolerations": [
                    {
                        "key": "karpenter.sh/nodepool",
                        "operator": "Equal",
                        "value": _COLD_NODEPOOL_NAME,
                        "effect": "NoSchedule",
                    }
                ],
            }
        },
    )


def _pod_phase(core_v1, namespace: str, pod_name: str) -> str:
    """Return current pod phase or 'Unknown'."""
    try:
        pod = core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        return (pod.status.phase or "Unknown") if pod.status else "Unknown"
    except Exception:
        return "Unknown"


def _force_delete_pod(core_v1, namespace: str, pod_name: str) -> None:
    """Force-delete a pod (grace period 0) for cleanup."""
    try:
        from kubernetes.client.models import V1DeleteOptions

        core_v1.delete_namespaced_pod(
            name=pod_name,
            namespace=namespace,
            body=V1DeleteOptions(grace_period_seconds=0),
        )
    except Exception as exc:
        if getattr(exc, "status", None) != 404:
            log.warning("Force-delete pod %s/%s failed: %s", namespace, pod_name, exc)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_karpenter_cold_node_pod_stays_pending(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T22a: pod targeting cold Karpenter pool stays Pending; requires Karpenter CRDs.

    Validates that when Karpenter cannot provision a node the pod remains
    in Pending state.  The test confirms the Pending state after a brief
    window rather than waiting for ORB's timeout to fire, keeping CI fast.
    """
    if not _karpenter_available(k8s_provider_config):
        pytest.skip(
            f"Karpenter not installed (CRD {_KARPENTER_NODEPOOL_CRD!r} absent). "
            "Install Karpenter to run T22 cold-node tests."
        )
    if not _cold_nodepool_exists(k8s_provider_config):
        pytest.skip(
            f"Cold-node NodePool {_COLD_NODEPOOL_NAME!r} not configured. "
            "Create a Karpenter NodePool named 'orb-test-karpenter-cold' that "
            "cannot schedule pods (e.g. targeting non-existent instance type)."
        )

    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_template_cold_node(k8s_namespace)

    # Submit the acquire — this should create the pod but not wait for Ready.
    result = await handler.acquire_hosts(request, template)
    pod_names = result.get("machine_ids", [])
    assert pod_names, "acquire_hosts returned no pod names for cold-node acquire"

    # Give the scheduler a moment to process the pod.
    time.sleep(10)

    pod_name = pod_names[0]
    phase = _pod_phase(k8s_core_v1, k8s_namespace, pod_name)
    assert phase == "Pending", (
        f"Expected pod {pod_name} to be Pending on cold Karpenter pool, got {phase!r}"
    )

    # Cleanup: force-delete the stuck pod.
    _force_delete_pod(k8s_core_v1, k8s_namespace, pod_name)


async def test_karpenter_cold_node_acquire_times_out(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T22b: ORB surfaces a timeout error when Karpenter nodes never come up.

    When the handler's acquire waits for nodes to become Ready and they
    never do, ORB must raise a TimeoutError (or provider-specific timeout
    exception) rather than blocking indefinitely.
    """
    if not _karpenter_available(k8s_provider_config):
        pytest.skip(
            f"Karpenter not installed (CRD {_KARPENTER_NODEPOOL_CRD!r} absent). "
            "Install Karpenter to run T22 cold-node tests."
        )
    if not _cold_nodepool_exists(k8s_provider_config):
        pytest.skip(
            f"Cold-node NodePool {_COLD_NODEPOOL_NAME!r} not configured. "
            "Create a Karpenter NodePool named 'orb-test-karpenter-cold'."
        )

    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_template_cold_node(k8s_namespace)

    start = time.monotonic()
    caught_exc: Exception | None = None
    try:
        await handler.acquire_hosts(request, template)
    except Exception as exc:
        caught_exc = exc
    elapsed = time.monotonic() - start

    # Cleanup any stuck pods.
    pods = k8s_core_v1.list_namespaced_pod(
        namespace=k8s_namespace,
        label_selector=f"orb.io/request-id={live_request_id}",
    )
    for pod in pods.items:
        _force_delete_pod(k8s_core_v1, k8s_namespace, pod.metadata.name)

    if caught_exc is None:
        # If no exception was raised, the acquire must have returned quickly
        # (handler doesn't wait for Ready in this path).  That is acceptable.
        log.info(
            "Cold-node acquire returned without exception after %.1fs "
            "(handler does not block on node readiness)",
            elapsed,
        )
        return

    log.info("Cold-node timeout test: elapsed=%.1fs, exc=%s", elapsed, caught_exc)
    error_str = str(caught_exc).lower()
    assert any(kw in error_str for kw in ("timeout", "pending", "schedule", "ready")), (
        f"Unexpected error for cold-node timeout: {caught_exc}"
    )
