"""kmock-backed tests for :class:`K8sStatefulSetHandler`.

Tests run the real kubernetes SDK against a kmock HTTP server.

Covered scenarios
-----------------

* acquire_hosts POSTs a StatefulSet with spec.replicas matching the count.
* release_hosts scale-down path patches spec.replicas targeting the
  highest ordinals (StatefulSet controller semantics).
* check_hosts_status reads pods from the apiserver and returns the correct
  fulfilment verdict (Group T1 backfill).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from kmock import KubernetesEmulator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    requested_count: int = 2,
    statefulset_name: str | None = None,
    namespace: str = "orb-test",
) -> Any:
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    provider_data: dict[str, Any] = {"namespace": namespace}
    if statefulset_name:
        provider_data["statefulset_name"] = statefulset_name
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="StatefulSet",
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data=provider_data,
    )


def _make_template(namespace: str = "orb-test") -> Any:
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="tpl-1",
        provider_type="k8s",
        provider_api="StatefulSet",
        image_id="busybox:latest",
        max_instances=5,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "container_image": "busybox:latest",
                "resource_requests": {"cpu": "100m", "memory": "64Mi"},
            }
        },
    )


def _make_sts_handler(k8s_client_facade: Any, k8s_config: Any) -> Any:
    from orb.providers.k8s.infrastructure.handlers.statefulset_handler import (
        K8sStatefulSetHandler,
    )

    return K8sStatefulSetHandler(
        kubernetes_client=k8s_client_facade,
        config=k8s_config,
        logger=MagicMock(),
    )


def _register_statefulsets_resource(kmock_k8s: KubernetesEmulator) -> Any:
    from kmock import resource

    sts_res = resource("apps", "v1", "statefulsets")
    kmock_k8s.resources[sts_res] = {
        "namespaced": True,
        "kind": "StatefulSet",
        "singular": "statefulset",
        "verbs": ["get", "list", "create", "patch", "delete", "watch"],
        "shortnames": ["sts"],
        "categories": [],
        "subresources": ["scale", "status"],
    }
    return sts_res


def _preload_statefulset(
    kmock_k8s: KubernetesEmulator,
    *,
    name: str,
    namespace: str = "orb-test",
    spec_replicas: int = 2,
) -> None:
    from kmock import resource

    sts_res = resource("apps", "v1", "statefulsets")
    # V1StatefulSetSpec requires both `selector` and `template` to be non-None
    # for the kubernetes SDK model to deserialise without raising.
    kmock_k8s.objects[sts_res, namespace, name] = {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "replicas": spec_replicas,
            "serviceName": name,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {"containers": [{"name": "app", "image": "busybox:latest"}]},
            },
        },
        "status": {
            "replicas": spec_replicas,
            "readyReplicas": spec_replicas,
        },
    }


# ---------------------------------------------------------------------------
# acquire_hosts — POST /apis/apps/v1/namespaces/<ns>/statefulsets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_statefulset_handler_creates_with_replicas(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """acquire_hosts creates a StatefulSet with spec.replicas == requested_count."""
    sts_res = _register_statefulsets_resource(kmock_k8s)

    handler = _make_sts_handler(k8s_client_facade, k8s_config)
    request = _make_request(requested_count=3)
    template = _make_template()

    result = await handler.acquire_hosts(request, template)

    assert len(result["resource_ids"]) == 1
    sts_name = result["resource_ids"][0]
    assert sts_name.startswith("orb-")
    assert result["provider_data"]["replicas"] == 3

    stored = kmock_k8s.objects[sts_res, "orb-test", sts_name]
    assert stored is not None
    assert not stored.deleted


@pytest.mark.asyncio
async def test_statefulset_handler_creates_with_correct_replica_count(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """The spec.replicas stored in kmock matches the requested count."""
    sts_res = _register_statefulsets_resource(kmock_k8s)

    handler = _make_sts_handler(k8s_client_facade, k8s_config)
    request = _make_request(requested_count=5)
    template = _make_template()

    result = await handler.acquire_hosts(request, template)
    sts_name = result["resource_ids"][0]

    stored = kmock_k8s.objects[sts_res, "orb-test", sts_name]
    assert stored.raw["spec"]["replicas"] == 5


# ---------------------------------------------------------------------------
# release_hosts — ordinal-aware scale-down
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_statefulset_handler_scale_down_from_highest_ordinal(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """release_hosts patches spec.replicas via the scale sub-resource.

    The StatefulSet controller terminates pods from the highest ordinal
    downwards.  The handler computes new_replicas = current - len(victims)
    and patches the scale sub-resource.

    We verify that the StatefulSet's spec.replicas was updated by the
    handler, which means it issued a PATCH to the scale sub-resource.
    The emulator reflects the PATCH via the merge-dict semantics on the
    stored object (the sub-resource path writes back to the root object).

    Note: kmock does not implement sub-resource routing; the scale PATCH
    is served by the catch-all PATCH handler and merged into the stored
    object.  This is sufficient to verify the handler made the correct call.
    """
    sts_res = _register_statefulsets_resource(kmock_k8s)

    sts_name = f"orb-{str(uuid.uuid4())[:8]}"
    _preload_statefulset(kmock_k8s, name=sts_name, spec_replicas=4)

    handler = _make_sts_handler(k8s_client_facade, k8s_config)
    handler._max_retries = 1

    request = _make_request(
        requested_count=4,
        statefulset_name=sts_name,
        namespace="orb-test",
    )

    # The two victims are the highest ordinals: <sts>-2 and <sts>-3.
    await handler.release_hosts([f"{sts_name}-2", f"{sts_name}-3"], request.provider_data)

    # After the release the spec.replicas stored in kmock must be 2.
    stored = kmock_k8s.objects[sts_res, "orb-test", sts_name]
    assert stored.raw["spec"]["replicas"] == 2


# ---------------------------------------------------------------------------
# check_hosts_status — reads pods via label-selector + StatefulSet status
# ---------------------------------------------------------------------------


def _preload_sts_pods(
    kmock_k8s: KubernetesEmulator,
    *,
    sts_name: str,
    request_id: str,
    namespace: str = "orb-test",
    count: int = 2,
    phase: str = "Running",
    ready: bool = True,
) -> list[str]:
    """Seed kmock with pods belonging to a StatefulSet-managed request."""
    from kmock import resource

    pod_res = resource("", "v1", "pods")
    names = []
    for i in range(count):
        pod_name = f"{sts_name}-{i}"
        names.append(pod_name)
        kmock_k8s.objects[pod_res, namespace, pod_name] = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": namespace,
                "labels": {
                    "orb.io/managed": "true",
                    "orb.io/request-id": request_id,
                    "orb.io/provider-api": "StatefulSet",
                },
            },
            "spec": {
                "containers": [{"name": "app", "image": "busybox:latest"}],
            },
            "status": {
                "phase": phase,
                "podIP": "10.0.0.2" if phase == "Running" else None,
                "hostIP": "10.1.0.2" if phase == "Running" else None,
                "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
                "containerStatuses": [],
            },
        }
    return names


def _make_request_with_id(
    request_id: str,
    *,
    requested_count: int = 2,
    statefulset_name: str | None = None,
    namespace: str = "orb-test",
) -> Any:
    """Build a real Request aggregate with the given string request_id."""
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    provider_data: dict[str, Any] = {"namespace": namespace}
    if statefulset_name:
        provider_data["statefulset_name"] = statefulset_name
    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="StatefulSet",
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data=provider_data,
    )


@pytest.mark.asyncio
async def test_statefulset_handler_check_status_running_pods(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """check_hosts_status sees Running pods and reports running instances.

    The StatefulSet object itself is absent here, so the controller view is
    empty.  The verdict is derived from the pod roll-up.
    """
    import asyncio

    from kmock import resource

    request_id = f"req-{uuid.uuid4()}"
    sts_name = f"orb-{str(uuid.uuid4())[:8]}"

    sts_res = resource("apps", "v1", "statefulsets")
    kmock_k8s.resources[sts_res] = {
        "namespaced": True,
        "kind": "StatefulSet",
        "singular": "statefulset",
        "verbs": ["get", "list", "create", "patch", "delete", "watch"],
        "shortnames": ["sts"],
        "categories": [],
        "subresources": ["scale", "status"],
    }
    pod_res = resource("", "v1", "pods")
    kmock_k8s.resources[pod_res] = {
        "namespaced": True,
        "kind": "Pod",
        "singular": "pod",
        "verbs": ["get", "list", "create", "delete", "watch"],
        "shortnames": ["po"],
        "categories": [],
        "subresources": [],
    }

    _preload_sts_pods(
        kmock_k8s,
        sts_name=sts_name,
        request_id=request_id,
        count=2,
        phase="Running",
        ready=True,
    )

    handler = _make_sts_handler(k8s_client_facade, k8s_config)
    request = _make_request_with_id(request_id, requested_count=2, statefulset_name=sts_name)

    result = await asyncio.to_thread(handler.check_hosts_status, request)

    assert len(result.instances) == 2
    for inst in result.instances:
        assert inst["status"] == "running"


@pytest.mark.asyncio
async def test_statefulset_handler_check_status_no_pods_in_progress(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """check_hosts_status with no pods returns in_progress (StatefulSet starting)."""
    import asyncio

    from kmock import resource

    request_id = f"req-{uuid.uuid4()}"
    sts_name = f"orb-{str(uuid.uuid4())[:8]}"

    sts_res = resource("apps", "v1", "statefulsets")
    kmock_k8s.resources[sts_res] = {
        "namespaced": True,
        "kind": "StatefulSet",
        "singular": "statefulset",
        "verbs": ["get", "list", "create", "patch", "delete", "watch"],
        "shortnames": ["sts"],
        "categories": [],
        "subresources": ["scale", "status"],
    }
    pod_res = resource("", "v1", "pods")
    kmock_k8s.resources[pod_res] = {
        "namespaced": True,
        "kind": "Pod",
        "singular": "pod",
        "verbs": ["get", "list", "create", "delete", "watch"],
        "shortnames": ["po"],
        "categories": [],
        "subresources": [],
    }

    handler = _make_sts_handler(k8s_client_facade, k8s_config)
    request = _make_request_with_id(request_id, requested_count=2, statefulset_name=sts_name)

    result = await asyncio.to_thread(handler.check_hosts_status, request)

    assert result.instances == []
    assert result.fulfilment.state == "in_progress"
