"""Unit tests for :class:`KubernetesDeploymentHandler` (Phase D).

Mocks ``CoreV1Api`` and ``AppsV1Api`` so no cluster is required.
Covers:

* ``acquire_hosts`` creates a single Deployment with ``spec.replicas=N``
  and persists the deployment name in ``provider_data``.
* ``release_hosts`` selective path: annotates each victim with the
  pod-deletion-cost annotation and patches replicas down by the victim
  count, without ever calling ``delete_namespaced_pod`` directly.
* ``release_hosts`` full-release path: patches replicas to zero and
  deletes the Deployment.
* ``check_hosts_status`` reads both the pod list and the Deployment
  status, rolling up via the controller's ``ready_replicas`` view.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.domain.template.template_aggregate import Template
from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
from orb.providers.kubernetes.handlers.deployment_handler import (
    POD_DELETION_COST_ANNOTATION,
    VICTIM_DELETION_COST,
    KubernetesDeploymentHandler,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    requested_count: int = 3,
    request_id: str | None = None,
    deployment_name: str | None = None,
    namespace: str = "orb-test",
) -> Request:
    provider_data: dict[str, Any] = {"namespace": namespace}
    if deployment_name:
        provider_data["deployment_name"] = deployment_name
    return Request(
        request_id=RequestId(value=request_id or f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="kubernetes",
        provider_api="KubernetesDeployment",
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data=provider_data,
    )


def _make_template() -> Template:
    return Template(
        template_id="tpl-1",
        provider_type="kubernetes",
        provider_api="KubernetesDeployment",
        image_id="busybox:latest",
        max_instances=5,
        provider_data={
            "kubernetes": {
                "namespace": "orb-test",
                "container_image": "busybox:latest",
                "resource_requests": {"cpu": "100m", "memory": "64Mi"},
            }
        },
    )


def _make_client(
    core_v1: Any | None = None,
    apps_v1: Any | None = None,
) -> MagicMock:
    client = MagicMock()
    client.core_v1 = core_v1 if core_v1 is not None else MagicMock()
    client.apps_v1 = apps_v1 if apps_v1 is not None else MagicMock()
    return client


def _make_handler(client: Any | None = None) -> KubernetesDeploymentHandler:
    if client is None:
        client = _make_client()
    config = KubernetesProviderConfig(namespace="orb-test")
    return KubernetesDeploymentHandler(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
    )


def _make_pod(*, name: str, phase: str, ready: bool = False) -> SimpleNamespace:
    conditions: list[SimpleNamespace] = []
    if ready:
        conditions.append(SimpleNamespace(type="Ready", status="True", reason=None))
    else:
        conditions.append(SimpleNamespace(type="Ready", status="False", reason=None))
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace="orb-test",
            labels={"orb.io/request-id": "req-abc"},
        ),
        spec=SimpleNamespace(node_name="node-1"),
        status=SimpleNamespace(
            phase=phase,
            pod_ip="10.0.0.1" if phase == "Running" else None,
            host_ip="10.1.0.1" if phase == "Running" else None,
            start_time=None,
            conditions=conditions,
            container_statuses=[],
        ),
    )


def _make_deployment_status(
    *,
    spec_replicas: int,
    ready_replicas: int | None = None,
    available_replicas: int | None = None,
    updated_replicas: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(name="orb-deadbeef", namespace="orb-test"),
        spec=SimpleNamespace(replicas=spec_replicas),
        status=SimpleNamespace(
            available_replicas=available_replicas,
            ready_replicas=ready_replicas,
            updated_replicas=updated_replicas,
            conditions=[],
        ),
    )


# ---------------------------------------------------------------------------
# acquire_hosts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_hosts_creates_single_deployment_with_replicas() -> None:
    apps_v1 = MagicMock()
    apps_v1.create_namespaced_deployment.return_value = SimpleNamespace()
    client = _make_client(apps_v1=apps_v1)
    handler = _make_handler(client=client)

    request = _make_request(requested_count=4)
    template = _make_template()

    result = await handler.acquire_hosts(request, template)

    # Exactly one Deployment created.
    assert apps_v1.create_namespaced_deployment.call_count == 1
    call_kwargs = apps_v1.create_namespaced_deployment.call_args.kwargs
    assert call_kwargs["namespace"] == "orb-test"
    body = call_kwargs["body"]
    assert body.spec.replicas == 4
    # resource_ids carries the deployment name; machine_ids stay empty
    # because the controller assigns pod names asynchronously.
    assert len(result["resource_ids"]) == 1
    assert result["resource_ids"][0].startswith("orb-")
    assert result["machine_ids"] == []
    assert result["provider_data"]["replicas"] == 4
    assert result["provider_data"]["namespace"] == "orb-test"
    assert result["provider_data"]["deployment_name"] == result["resource_ids"][0]


@pytest.mark.asyncio
async def test_acquire_hosts_replicas_floors_at_one() -> None:
    apps_v1 = MagicMock()
    apps_v1.create_namespaced_deployment.return_value = SimpleNamespace()
    client = _make_client(apps_v1=apps_v1)
    handler = _make_handler(client=client)

    request = _make_request(requested_count=0)
    template = _make_template()

    await handler.acquire_hosts(request, template)
    body = apps_v1.create_namespaced_deployment.call_args.kwargs["body"]
    # Even when requested_count is 0 or negative, we never submit a
    # zero-replica Deployment on acquire — that path is reserved for
    # the full-release sequence.
    assert body.spec.replicas == 1


# ---------------------------------------------------------------------------
# release_hosts — selective via pod-deletion-cost + replicas patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_hosts_selective_annotates_victims_and_patches_replicas() -> None:
    core_v1 = MagicMock()
    core_v1.patch_namespaced_pod.return_value = SimpleNamespace()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_status(spec_replicas=5)
    apps_v1.patch_namespaced_deployment_scale.return_value = SimpleNamespace()

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)

    request = _make_request(
        requested_count=5,
        deployment_name="orb-deadbeef",
        namespace="orb-test",
    )

    await handler.release_hosts(["pod-m2", "pod-m4"], request)

    # Step 1: both victim pods received the annotation with cost ``-9999``.
    assert core_v1.patch_namespaced_pod.call_count == 2
    annotated_pods = set()
    for call in core_v1.patch_namespaced_pod.call_args_list:
        kwargs = call.kwargs
        annotated_pods.add(kwargs["name"])
        body = kwargs["body"]
        annotations = body["metadata"]["annotations"]
        assert annotations[POD_DELETION_COST_ANNOTATION] == VICTIM_DELETION_COST
        assert kwargs["namespace"] == "orb-test"
    assert annotated_pods == {"pod-m2", "pod-m4"}

    # Step 2: replicas patched from 5 -> 3 (5 minus 2 victims).
    apps_v1.patch_namespaced_deployment_scale.assert_called_once()
    patch_kwargs = apps_v1.patch_namespaced_deployment_scale.call_args.kwargs
    assert patch_kwargs["name"] == "orb-deadbeef"
    assert patch_kwargs["namespace"] == "orb-test"
    assert patch_kwargs["body"]["spec"]["replicas"] == 3

    # Pods are NEVER deleted directly — the controller decides which
    # pods to evict, using the deletion-cost annotation to order them.
    core_v1.delete_namespaced_pod.assert_not_called()
    # And we never delete the Deployment itself on a selective release.
    apps_v1.delete_namespaced_deployment.assert_not_called()


@pytest.mark.asyncio
async def test_release_hosts_selective_404_on_victim_is_best_effort() -> None:
    from kubernetes.client.exceptions import ApiException

    core_v1 = MagicMock()

    def _patch(*, name: str, namespace: str, body: Any) -> None:
        raise ApiException(status=404, reason="Not Found")

    core_v1.patch_namespaced_pod.side_effect = _patch
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_status(spec_replicas=2)
    apps_v1.patch_namespaced_deployment_scale.return_value = SimpleNamespace()

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)
    handler._max_retries = 1

    request = _make_request(
        requested_count=2,
        deployment_name="orb-deadbeef",
        namespace="orb-test",
    )

    # 404 on the annotation patch must not raise — selective release
    # is best-effort on the per-pod patch step.  The replicas patch
    # still proceeds (current=2, victims=1 => new=1).
    await handler.release_hosts(["pod-x"], request)
    apps_v1.patch_namespaced_deployment_scale.assert_called_once()
    assert (
        apps_v1.patch_namespaced_deployment_scale.call_args.kwargs["body"]["spec"]["replicas"] == 1
    )


# ---------------------------------------------------------------------------
# release_hosts — full release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_hosts_full_release_scales_to_zero_then_deletes() -> None:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_status(spec_replicas=3)
    apps_v1.patch_namespaced_deployment_scale.return_value = SimpleNamespace()
    apps_v1.delete_namespaced_deployment.return_value = SimpleNamespace()

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)

    request = _make_request(
        requested_count=3,
        deployment_name="orb-deadbeef",
        namespace="orb-test",
    )

    await handler.release_hosts(["pod-a", "pod-b", "pod-c"], request)

    # Full release path: no annotation step, just scale-to-zero + delete.
    core_v1.patch_namespaced_pod.assert_not_called()
    apps_v1.patch_namespaced_deployment_scale.assert_called_once()
    assert (
        apps_v1.patch_namespaced_deployment_scale.call_args.kwargs["body"]["spec"]["replicas"] == 0
    )
    apps_v1.delete_namespaced_deployment.assert_called_once()


@pytest.mark.asyncio
async def test_release_hosts_empty_machine_ids_is_noop() -> None:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)

    request = _make_request()
    await handler.release_hosts([], request)

    apps_v1.read_namespaced_deployment.assert_not_called()
    core_v1.patch_namespaced_pod.assert_not_called()
    apps_v1.patch_namespaced_deployment_scale.assert_not_called()


@pytest.mark.asyncio
async def test_release_hosts_deployment_already_gone_is_best_effort() -> None:
    from kubernetes.client.exceptions import ApiException

    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.side_effect = ApiException(status=404, reason="Not Found")

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)
    handler._max_retries = 1

    request = _make_request(deployment_name="orb-deadbeef")
    # Must not raise — deployment evaporated, treat as success.
    await handler.release_hosts(["pod-x"], request)
    core_v1.patch_namespaced_pod.assert_not_called()
    apps_v1.patch_namespaced_deployment_scale.assert_not_called()


# ---------------------------------------------------------------------------
# check_hosts_status
# ---------------------------------------------------------------------------


def test_check_hosts_status_fulfilled_uses_controller_view() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-aaa-r1", phase="Running", ready=True),
            _make_pod(name="orb-aaa-r2", phase="Running", ready=True),
        ]
    )
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_status(
        spec_replicas=2, ready_replicas=2, available_replicas=2, updated_replicas=2
    )

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)
    request = _make_request(requested_count=2, deployment_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    assert isinstance(result, CheckHostsStatusResult)
    assert isinstance(result.fulfilment, ProviderFulfilment)
    assert result.fulfilment.state == "fulfilled"
    assert result.fulfilment.target_units == 2
    assert result.fulfilment.running_count == 2


def test_check_hosts_status_in_progress_when_replicas_not_ready() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-aaa-r1", phase="Running", ready=True),
            _make_pod(name="orb-aaa-r2", phase="Pending"),
        ]
    )
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_status(
        spec_replicas=2, ready_replicas=1, available_replicas=1
    )

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)
    request = _make_request(requested_count=2, deployment_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "in_progress"
    assert result.fulfilment.running_count == 1
    assert result.fulfilment.pending_count == 1


def test_check_hosts_status_partial_after_selective_scale_down() -> None:
    """After a successful selective release the controller view should drive
    a ``partial`` state rather than ``in_progress``."""
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-aaa-r1", phase="Running", ready=True),
        ]
    )
    apps_v1 = MagicMock()
    # Original requested_count was 2; one pod was released so the
    # controller now reports 1 ready.
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_status(
        spec_replicas=1, ready_replicas=1, available_replicas=1
    )

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)
    request = _make_request(requested_count=2, deployment_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "partial"
    assert result.fulfilment.running_count == 1
    assert result.fulfilment.target_units == 2


def test_check_hosts_status_deployment_missing_falls_back_to_pod_rollup() -> None:
    """If the Deployment is gone but pods are still terminating, the
    handler should still produce a sensible roll-up from the pod list."""
    from kubernetes.client.exceptions import ApiException

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.side_effect = ApiException(status=404, reason="Not Found")

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)
    handler._max_retries = 1
    request = _make_request(requested_count=2, deployment_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    # No pods + no deployment + non-zero target => still in_progress
    # so callers retry rather than failing.
    assert result.fulfilment.state == "in_progress"
    assert result.instances == []


def test_check_hosts_status_handles_list_failure() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.side_effect = RuntimeError("apiserver down")
    apps_v1 = MagicMock()
    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)
    handler._max_retries = 1
    request = _make_request(requested_count=2, deployment_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "in_progress"
    assert "apiserver down" in result.fulfilment.message


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------


def test_get_example_templates_returns_deployment_example() -> None:
    examples = KubernetesDeploymentHandler.get_example_templates()
    assert len(examples) >= 1
    example = examples[0]
    assert example.provider_api == "KubernetesDeployment"
    assert example.provider_type == "kubernetes"
    assert example.image_id == "busybox:latest"


# ---------------------------------------------------------------------------
# Provider-API key
# ---------------------------------------------------------------------------


def test_provider_api_key_matches_value_object() -> None:
    """The handler's PROVIDER_API key must match the enum value used
    by the strategy dispatch."""
    from orb.providers.kubernetes.value_objects import KubernetesProviderApi

    assert KubernetesDeploymentHandler.PROVIDER_API == KubernetesProviderApi.DEPLOYMENT.value
