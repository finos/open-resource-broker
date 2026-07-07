"""Live integration tests for T25: ARM64 nodeAffinity scheduling.

Tests in this module hit a real Kubernetes cluster.  They are skipped by
default; pass ``--run-k8s`` to enable them.

Scenario: ORB templates can declare nodeAffinity rules targeting ARM64 nodes
(``kubernetes.io/arch=arm64``).  Pods must be scheduled exclusively onto
ARM64 nodes and the handler must report them as running.  Tests are skipped
when the cluster has no ARM64 nodes available.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.arm64_nodeaffinity")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_ARM64_ARCH_LABEL = "kubernetes.io/arch"
_ARM64_VALUE = "arm64"
_READY_TIMEOUT = 180  # seconds — ARM64 cold-start can be slower
_POLL_INTERVAL = 5  # seconds


# ---------------------------------------------------------------------------
# Skip helpers
# ---------------------------------------------------------------------------


def _arm64_nodes_available(k8s_provider_config: dict) -> bool:
    """Return True when at least one Ready ARM64 node exists in the cluster."""
    try:
        from kubernetes import client as k8s_client_mod, config as k8s_config_mod

        kubeconfig_path = k8s_provider_config.get("kubeconfig_path")
        context = k8s_provider_config.get("context")
        k8s_config_mod.load_kube_config(config_file=kubeconfig_path, context=context)
        core_v1 = k8s_client_mod.CoreV1Api()
        nodes = core_v1.list_node(label_selector=f"{_ARM64_ARCH_LABEL}={_ARM64_VALUE}")
        for node in nodes.items:
            conditions = (node.status.conditions or []) if node.status else []
            for cond in conditions:
                if cond.type == "Ready" and cond.status == "True":
                    return True
        return False
    except Exception as exc:
        log.debug("ARM64 node availability check failed: %s", exc)
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


def _make_request(request_id: str, count: int = 1, template_id: str = "live-arm64-tpl"):
    """Construct a minimal Request."""
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


def _make_arm64_template(namespace: str):
    """Build a Template with ARM64 nodeAffinity."""
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="live-arm64-tpl",
        provider_type="k8s",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=5,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "command": ["sh", "-c", "uname -m && sleep 3600"],
                "affinity": {
                    "nodeAffinity": {
                        "requiredDuringSchedulingIgnoredDuringExecution": {
                            "nodeSelectorTerms": [
                                {
                                    "matchExpressions": [
                                        {
                                            "key": _ARM64_ARCH_LABEL,
                                            "operator": "In",
                                            "values": [_ARM64_VALUE],
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                },
            }
        },
    )


def _wait_pod_running(
    core_v1, namespace: str, pod_name: str, timeout: float = _READY_TIMEOUT
) -> str:
    """Poll until pod reaches Running (or Failed/Succeeded) phase."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            pod = core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
            phase = (pod.status.phase or "Unknown") if pod.status else "Unknown"
            if phase in {"Running", "Succeeded", "Failed"}:
                return phase
        except Exception as exc:
            if getattr(exc, "status", None) != 404:
                raise
            # 404 during the readiness poll means the pod was reaped
            # (e.g. Karpenter timed out) — swallow so the outer deadline
            # trips and reports a clean TimeoutError.
            log.debug("readiness poll saw 404 for %s/%s; continuing", namespace, pod_name)
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Pod {namespace}/{pod_name} did not reach Running within {timeout}s"
            )
        time.sleep(_POLL_INTERVAL)


def _get_pod_node_arch(core_v1, namespace: str, pod_name: str) -> str | None:
    """Return the kubernetes.io/arch label value of the node hosting the pod."""
    try:
        pod = core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        node_name = pod.spec.node_name if pod.spec else None
        if not node_name:
            return None
        node = core_v1.read_node(name=node_name)
        return (node.metadata.labels or {}).get(_ARM64_ARCH_LABEL)
    except Exception as exc:
        log.debug("get_pod_node_arch failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_arm64_pod_scheduled_on_arm64_node(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T25a: pod with ARM64 nodeAffinity is scheduled onto an ARM64 node.

    Requires at least one Ready ARM64 node in the cluster.  Skipped otherwise.
    """
    if not _arm64_nodes_available(k8s_provider_config):
        pytest.skip(
            f"No Ready ARM64 nodes found (label {_ARM64_ARCH_LABEL}={_ARM64_VALUE}). "
            "Add ARM64 nodes to the cluster to run T25 nodeAffinity tests."
        )

    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_arm64_template(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    pod_names = result.get("machine_ids", [])
    assert pod_names, "acquire_hosts returned no pod names for ARM64 acquire"

    pod_name = pod_names[0]
    phase = _wait_pod_running(k8s_core_v1, k8s_namespace, pod_name)
    assert phase == "Running", f"ARM64 pod reached phase {phase!r} instead of Running"

    node_arch = _get_pod_node_arch(k8s_core_v1, k8s_namespace, pod_name)
    assert node_arch == _ARM64_VALUE, (
        f"Pod {pod_name} landed on node with arch={node_arch!r}; expected {_ARM64_VALUE!r}. "
        "nodeAffinity constraint was not honoured."
    )

    # Cleanup.
    try:
        await handler.release_hosts(pod_names, request.provider_data)
    except Exception as exc:
        log.warning("Cleanup release failed: %s", exc)


async def test_arm64_template_rejected_when_no_arm64_nodes(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T25b: pod with ARM64 nodeAffinity stays Pending on amd64-only clusters.

    When there are no ARM64 nodes the pod must remain Pending (unschedulable),
    not silently land on an amd64 node.  The test is skipped on clusters that
    DO have ARM64 nodes (T25a covers those).
    """
    if _arm64_nodes_available(k8s_provider_config):
        pytest.skip(
            "Cluster has ARM64 nodes available — T25b only applies to amd64-only clusters. "
            "Run T25a instead."
        )

    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_arm64_template(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    pod_names = result.get("machine_ids", [])
    assert pod_names, "No pod created for ARM64 acquire on amd64-only cluster"

    pod_name = pod_names[0]
    # Give scheduler time to decide.
    time.sleep(15)

    pod = k8s_core_v1.read_namespaced_pod(name=pod_name, namespace=k8s_namespace)
    phase = (pod.status.phase or "Unknown") if pod.status else "Unknown"
    assert phase == "Pending", (
        f"Expected pod to be Pending on amd64-only cluster (nodeAffinity requires arm64), "
        f"got phase={phase!r}"
    )

    # Cleanup: delete the stuck pod.
    try:
        k8s_core_v1.delete_namespaced_pod(name=pod_name, namespace=k8s_namespace)
    except Exception as _exc:
        log.debug("cleanup swallowed: %s", _exc)


async def test_arm64_multi_pod_all_land_on_arm64(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T25c: all pods in a multi-pod ARM64 acquire land on ARM64 nodes.

    Acquires 2 pods with ARM64 nodeAffinity and asserts each lands on an
    ARM64 node.  Requires at least 2 Ready ARM64 nodes.
    """
    if not _arm64_nodes_available(k8s_provider_config):
        pytest.skip(f"No Ready ARM64 nodes found (label {_ARM64_ARCH_LABEL}={_ARM64_VALUE}).")

    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=2)
    template = _make_arm64_template(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    pod_names = result.get("machine_ids", [])
    assert pod_names, "No pods created for multi-pod ARM64 acquire"

    wrong_arch_pods: list[tuple[str, str | None]] = []
    for pod_name in pod_names:
        phase = _wait_pod_running(k8s_core_v1, k8s_namespace, pod_name, timeout=_READY_TIMEOUT)
        if phase != "Running":
            log.warning("Pod %s reached phase %r, skipping arch check", pod_name, phase)
            continue
        node_arch = _get_pod_node_arch(k8s_core_v1, k8s_namespace, pod_name)
        if node_arch != _ARM64_VALUE:
            wrong_arch_pods.append((pod_name, node_arch))

    # Cleanup.
    try:
        await handler.release_hosts(pod_names, request.provider_data)
    except Exception as exc:
        log.warning("Cleanup release failed: %s", exc)

    assert not wrong_arch_pods, f"The following pods landed on non-ARM64 nodes: {wrong_arch_pods}"
