"""Live integration tests for :class:`K8sStatefulSetHandler`.

Tests in this module hit a real Kubernetes cluster.  Pass ``--run-k8s``
to enable them.

StatefulSet semantics: the controller assigns pod names with ascending
ordinals (``<name>-0``, ``<name>-1``, ...) and always scales down by
removing the highest-ordinal pod first.  These tests verify that
contract on a live cluster.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.statefulset")

pytestmark = [pytest.mark.asyncio]

_POD_READY_TIMEOUT = 180  # seconds
_SCALE_TIMEOUT = 120  # seconds
_POLL_INTERVAL = 5  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_statefulset_handler(k8s_provider_config: dict):
    """Construct a live :class:`K8sStatefulSetHandler`."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.handlers.statefulset_handler import K8sStatefulSetHandler
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
    return K8sStatefulSetHandler(kubernetes_client=client, config=config, logger=logger), config


def _make_request(request_id: str, count: int = 3):
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="StatefulSet",
        template_id="live-sts-tpl",
        requested_count=count,
        provider_data={},
    )


def _make_template(namespace: str, image: str = "busybox:latest"):
    """Construct a minimal :class:`K8sTemplate` for StatefulSet tests.

    Kubernetes-specific fields (namespace, command) are set as typed
    ``K8sTemplate`` attributes so they reach the pod spec — the generic
    ``Template`` aggregate has no ``provider_data`` surface, so those
    fields would be silently dropped if set there.  A long-running command
    keeps pods Running and Ready, which the StatefulSet controller requires
    (default ``OrderedReady`` policy) before it will honour a scale-down.
    """
    from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate

    return K8sTemplate(
        template_id="live-sts-tpl",
        provider_api="StatefulSet",
        machine_image=image,
        max_machines=10,
        namespace=namespace,
        command=["sh", "-c", "sleep 3600"],
    )


def _wait_for_pods_by_label(
    core_v1,
    namespace: str,
    label_selector: str,
    expected_count: int,
    timeout: float = _POD_READY_TIMEOUT,
) -> list:
    """Poll until at least ``expected_count`` pods exist for the label."""
    deadline = time.monotonic() + timeout
    pods: list = []
    while time.monotonic() < deadline:
        pod_list = core_v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
        pods = pod_list.items or []
        if len(pods) >= expected_count:
            return pods
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(
        f"Expected {expected_count} pods with {label_selector!r} within {timeout}s; "
        f"found {len(pods)}"
    )


def _wait_for_active_pod_count(
    core_v1,
    namespace: str,
    label_selector: str,
    expected_count: int,
    timeout: float = _SCALE_TIMEOUT,
) -> list:
    """Poll until exactly ``expected_count`` non-terminal pods remain."""
    deadline = time.monotonic() + timeout
    active: list = []
    while time.monotonic() < deadline:
        pod_list = core_v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
        active = [
            p
            for p in (pod_list.items or [])
            if (p.status.phase or "") not in ("Succeeded", "Failed")
            and p.metadata.deletion_timestamp is None
        ]
        if len(active) == expected_count:
            return active
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(
        f"Expected {expected_count} active pods with {label_selector!r} within {timeout}s; "
        f"found {len(active)}"
    )


def _statefulset_exists(apps_v1, namespace: str, sts_name: str) -> bool:
    try:
        apps_v1.read_namespaced_stateful_set(name=sts_name, namespace=namespace)
        return True
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            return False
        raise


def _wait_until_statefulset_gone(
    apps_v1, namespace: str, sts_name: str, timeout: float = _SCALE_TIMEOUT
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _statefulset_exists(apps_v1, namespace, sts_name):
            return
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"StatefulSet {namespace}/{sts_name} not deleted within {timeout}s")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_statefulset_acquire_creates_ordinals(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Acquire 3 via StatefulSet; verify pods are named ``<name>-0``, ``-1``, ``-2``."""
    from kubernetes import client as k8s_client_mod, config as k8s_config_mod

    kubeconfig_path = k8s_provider_config.get("kubeconfig_path")
    context = k8s_provider_config.get("context")
    k8s_config_mod.load_kube_config(config_file=kubeconfig_path, context=context)
    apps_v1 = k8s_client_mod.AppsV1Api()

    handler, _ = _build_statefulset_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=3)
    template = _make_template(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    sts_name = result["resource_ids"][0]
    request.provider_data = {  # type: ignore[assignment]
        "namespace": k8s_namespace,
        "statefulset_name": sts_name,
    }
    log.info("Acquired StatefulSet %s/%s", k8s_namespace, sts_name)

    label_selector = f"orb.io/request-id={live_request_id}"
    pods = _wait_for_pods_by_label(k8s_core_v1, k8s_namespace, label_selector, expected_count=3)

    pod_names = sorted(p.metadata.name for p in pods)
    expected_suffixes = {f"{sts_name}-0", f"{sts_name}-1", f"{sts_name}-2"}
    actual_names = set(pod_names)
    # The pod set should contain the three ordinal pods.
    assert expected_suffixes.issubset(actual_names), (
        f"Expected ordinal pods {expected_suffixes!r} in pod set {actual_names!r}"
    )

    # Full cleanup
    await handler.release_hosts(pod_names, request.provider_data)
    _wait_until_statefulset_gone(apps_v1, k8s_namespace, sts_name)


async def test_statefulset_release_terminates_highest_ordinal_first(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Release 1 of 3; verify the highest-ordinal pod (``<name>-2``) is removed."""
    from kubernetes import client as k8s_client_mod, config as k8s_config_mod

    kubeconfig_path = k8s_provider_config.get("kubeconfig_path")
    context = k8s_provider_config.get("context")
    k8s_config_mod.load_kube_config(config_file=kubeconfig_path, context=context)
    apps_v1 = k8s_client_mod.AppsV1Api()

    handler, _ = _build_statefulset_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=3)
    template = _make_template(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    sts_name = result["resource_ids"][0]
    request.provider_data = {  # type: ignore[assignment]
        "namespace": k8s_namespace,
        "statefulset_name": sts_name,
    }

    label_selector = f"orb.io/request-id={live_request_id}"
    _wait_for_pods_by_label(k8s_core_v1, k8s_namespace, label_selector, expected_count=3)
    # Release 1 — the handler scales down by 1 and the controller evicts the
    # highest-ordinal pod (``<sts_name>-2``).  The handler honours a release
    # only when the caller supplies the top-of-stack victim, so we pass the
    # highest-ordinal pod name explicitly.
    highest_pod = f"{sts_name}-2"
    await handler.release_hosts([highest_pod], request.provider_data)

    remaining = _wait_for_active_pod_count(
        k8s_core_v1, k8s_namespace, label_selector, expected_count=2
    )
    remaining_names = {p.metadata.name for p in remaining}

    # The controller must have evicted the highest ordinal (-2).
    assert f"{sts_name}-2" not in remaining_names, (
        f"Expected highest-ordinal pod {sts_name}-2 to be evicted; remaining={remaining_names!r}"
    )
    # The lowest-ordinal (-0) should still be there since the StatefulSet
    # controller always evicts highest-first.
    assert f"{sts_name}-0" in remaining_names, (
        f"Expected {sts_name}-0 to remain; remaining={remaining_names!r}"
    )

    # Full cleanup
    await handler.release_hosts(list(remaining_names), request.provider_data)
    _wait_until_statefulset_gone(apps_v1, k8s_namespace, sts_name)


async def test_statefulset_release_of_non_highest_is_refused(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Requesting release of a non-highest ordinal is refused.

    The StatefulSet controller only supports scale-down from the top of the
    ordinal stack, so the handler refuses a selective release whose victims
    are not exactly the top-of-stack pod names — silently evicting a
    different pod would cause data loss.  This test confirms the handler
    raises rather than evicting the wrong pod, and that all three pods
    remain after the refusal.
    """
    from kubernetes import client as k8s_client_mod, config as k8s_config_mod

    from orb.providers.k8s.exceptions.k8s_exceptions import K8sError

    kubeconfig_path = k8s_provider_config.get("kubeconfig_path")
    context = k8s_provider_config.get("context")
    k8s_config_mod.load_kube_config(config_file=kubeconfig_path, context=context)
    apps_v1 = k8s_client_mod.AppsV1Api()

    handler, _ = _build_statefulset_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=3)
    template = _make_template(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    sts_name = result["resource_ids"][0]
    request.provider_data = {  # type: ignore[assignment]
        "namespace": k8s_namespace,
        "statefulset_name": sts_name,
    }

    label_selector = f"orb.io/request-id={live_request_id}"
    _wait_for_pods_by_label(k8s_core_v1, k8s_namespace, label_selector, expected_count=3)

    # Request to release ordinal -1 specifically (a non-highest victim).  The
    # top-of-stack victim for a scale-down-by-1 would be ``<sts_name>-2``, so a
    # ``-1`` victim must be refused.
    middle_pod = f"{sts_name}-1"
    with pytest.raises(K8sError) as exc_info:
        await handler.release_hosts([middle_pod], request.provider_data)
    assert "top of the ordinal stack" in str(exc_info.value)

    # No pod should have been evicted — all three ordinals remain active.
    remaining = _wait_for_active_pod_count(
        k8s_core_v1, k8s_namespace, label_selector, expected_count=3
    )
    remaining_names = {p.metadata.name for p in remaining}
    assert remaining_names == {f"{sts_name}-0", f"{sts_name}-1", f"{sts_name}-2"}, (
        f"Refused release must not evict any pod; remaining={remaining_names!r}"
    )

    # Full cleanup
    await handler.release_hosts(list(remaining_names), request.provider_data)
    _wait_until_statefulset_gone(apps_v1, k8s_namespace, sts_name)
