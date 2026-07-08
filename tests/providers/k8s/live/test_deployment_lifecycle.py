"""Live integration tests for :class:`K8sDeploymentHandler`.

Tests in this module hit a real Kubernetes cluster.  Pass ``--run-k8s``
to enable them.

All Deployments created here carry ``orb.io/managed=true`` so the
session-level nuclear cleanup in ``conftest.py`` removes any strays.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.deployment")

pytestmark = [pytest.mark.asyncio]

_POD_READY_TIMEOUT = 180  # seconds
_SCALE_TIMEOUT = 120  # seconds
_POLL_INTERVAL = 5  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_deployment_handler(k8s_provider_config: dict):
    """Construct a live :class:`K8sDeploymentHandler`."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler
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
    return K8sDeploymentHandler(kubernetes_client=client, config=config, logger=logger), config


def _make_request(request_id: str, count: int = 3):
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Deployment",
        template_id="live-dep-tpl",
        requested_count=count,
        provider_data={},
    )


def _make_template(namespace: str, image: str = "busybox:latest"):
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="live-dep-tpl",
        provider_type="k8s",
        provider_api="Deployment",
        image_id=image,
        max_instances=10,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "command": ["sh", "-c", "sleep 3600"],
            }
        },
    )


def _wait_for_pods_by_owner(
    core_v1,
    namespace: str,
    label_selector: str,
    expected_count: int,
    timeout: float = _POD_READY_TIMEOUT,
) -> list:
    """Poll until at least ``expected_count`` pods matching ``label_selector`` exist."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pod_list = core_v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
        pods = pod_list.items or []
        if len(pods) >= expected_count:
            return pods
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(
        f"Expected {expected_count} pods with selector {label_selector!r} "
        f"in {namespace} within {timeout}s; found {len(pods)}"
    )


def _wait_for_pod_count(
    core_v1,
    namespace: str,
    label_selector: str,
    expected_count: int,
    timeout: float = _SCALE_TIMEOUT,
) -> None:
    """Poll until exactly ``expected_count`` non-terminated pods remain."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pod_list = core_v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
        active = [
            p
            for p in (pod_list.items or [])
            if (p.status.phase or "") not in ("Succeeded", "Failed")
            and p.metadata.deletion_timestamp is None
        ]
        if len(active) == expected_count:
            return
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(
        f"Expected {expected_count} active pods with selector {label_selector!r} within {timeout}s"
    )


def _deployment_exists(apps_v1, namespace: str, deployment_name: str) -> bool:
    try:
        apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        return True
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            return False
        raise


def _wait_until_deployment_gone(
    apps_v1, namespace: str, deployment_name: str, timeout: float = _SCALE_TIMEOUT
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _deployment_exists(apps_v1, namespace, deployment_name):
            return
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"Deployment {namespace}/{deployment_name} not deleted within {timeout}s")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_deployment_acquire_n_replicas(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Acquire 3 machines via Deployment; verify 3 pods exist with correct labels."""
    handler, _ = _build_deployment_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=3)
    template = _make_template(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    resource_ids = result.get("resource_ids", [])
    assert len(resource_ids) == 1, f"Expected 1 deployment name, got {resource_ids!r}"
    deployment_name = resource_ids[0]
    log.info("Acquired deployment %s/%s", k8s_namespace, deployment_name)

    # Poll until the controller has created all 3 pods.
    label_selector = f"orb.io/request-id={live_request_id}"
    pods = _wait_for_pods_by_owner(k8s_core_v1, k8s_namespace, label_selector, expected_count=3)
    assert len(pods) >= 3, f"Expected 3 pods, found {len(pods)}"

    # Verify each pod has an ownerReference pointing at a ReplicaSet (which
    # in turn is owned by the Deployment).  We check the label rather than
    # walking the ownerReference chain to keep the assertion simple.
    for pod in pods[:3]:
        assert pod.metadata.labels.get("orb.io/request-id") == live_request_id, (
            f"Pod {pod.metadata.name} missing request-id label"
        )

    # Cleanup — full release
    request.provider_data = {  # type: ignore[assignment]
        "namespace": k8s_namespace,
        "deployment_name": deployment_name,
    }
    pod_names = [p.metadata.name for p in pods[:3]]
    await handler.release_hosts(pod_names, request.provider_data)
    _wait_until_deployment_gone(
        __import__("kubernetes").client.AppsV1Api(), k8s_namespace, deployment_name
    )


async def test_deployment_selective_release_by_pod_deletion_cost(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Acquire 3 pods via Deployment; release 1; verify only 2 remain.

    The selective-release mechanism annotates the chosen pod with a
    negative ``controller.kubernetes.io/pod-deletion-cost`` and scales
    the Deployment replicas down by 1.  The cluster picks the annotated
    pod for termination.  We verify that exactly 2 pods remain after the
    scale-down completes.
    """
    handler, _ = _build_deployment_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=3)
    template = _make_template(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    deployment_name = result["resource_ids"][0]
    request.provider_data = {  # type: ignore[assignment]
        "namespace": k8s_namespace,
        "deployment_name": deployment_name,
    }

    label_selector = f"orb.io/request-id={live_request_id}"
    pods = _wait_for_pods_by_owner(k8s_core_v1, k8s_namespace, label_selector, expected_count=3)
    pod_names = [p.metadata.name for p in pods[:3]]

    # Release 1 — pick the first pod.
    victim = pod_names[:1]
    await handler.release_hosts(victim, request.provider_data)

    # Wait for the Deployment to scale down to 2.
    _wait_for_pod_count(k8s_core_v1, k8s_namespace, label_selector, expected_count=2)

    remaining_pods = k8s_core_v1.list_namespaced_pod(
        namespace=k8s_namespace, label_selector=label_selector
    ).items
    active_names = {
        p.metadata.name
        for p in remaining_pods
        if (p.status.phase or "") not in ("Succeeded", "Failed")
        and p.metadata.deletion_timestamp is None
    }
    assert len(active_names) == 2, (
        f"Expected 2 active pods after selective release, got {active_names!r}"
    )

    # Full cleanup
    remaining = list(active_names)
    await handler.release_hosts(remaining, request.provider_data)
    _wait_until_deployment_gone(
        __import__("kubernetes").client.AppsV1Api(), k8s_namespace, deployment_name
    )


async def test_deployment_full_release_deletes_deployment(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Release all machines from a Deployment; verify the Deployment resource is deleted."""
    from kubernetes import client as k8s_client_mod, config as k8s_config_mod

    # Re-load kubeconfig for the apps client within this test.
    kubeconfig_path = k8s_provider_config.get("kubeconfig_path")
    context = k8s_provider_config.get("context")
    k8s_config_mod.load_kube_config(config_file=kubeconfig_path, context=context)
    apps_v1 = k8s_client_mod.AppsV1Api()

    handler, _ = _build_deployment_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=2)
    template = _make_template(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    deployment_name = result["resource_ids"][0]
    request.provider_data = {  # type: ignore[assignment]
        "namespace": k8s_namespace,
        "deployment_name": deployment_name,
    }

    label_selector = f"orb.io/request-id={live_request_id}"
    pods = _wait_for_pods_by_owner(k8s_core_v1, k8s_namespace, label_selector, expected_count=2)
    pod_names = [p.metadata.name for p in pods[:2]]

    assert _deployment_exists(apps_v1, k8s_namespace, deployment_name), (
        f"Deployment {deployment_name} should exist before full release"
    )

    # Full release
    await handler.release_hosts(pod_names, request.provider_data)
    _wait_until_deployment_gone(apps_v1, k8s_namespace, deployment_name, timeout=_SCALE_TIMEOUT)

    assert not _deployment_exists(apps_v1, k8s_namespace, deployment_name), (
        f"Deployment {deployment_name} still exists after full release"
    )
