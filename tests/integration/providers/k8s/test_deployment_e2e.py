"""End-to-end integration test for the ``Deployment`` API.

Drives the strategy + Deployment handler through:

* ``acquire`` — single Deployment with ``spec.replicas = requested_count``;
* ``get_status`` — pod list + Deployment status rolled up via
  ``ready_replicas``;
* ``return_machines`` (selective) — pod-deletion-cost annotation
  applied to the named victims followed by a scale-down patch (no
  direct pod deletes);
* ``return_machines`` (full release) — scale-to-zero patch followed by
  Deployment deletion.

Asserts label propagation on the Deployment template AND on the pod
template, request_id linkage on the pod list result, and the status
transition Pending -> Available.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.base.operation_outcome import Accepted, Completed
from orb.providers.k8s.infrastructure.handlers.deployment_handler import (
    POD_DELETION_COST_ANNOTATION,
    VICTIM_DELETION_COST,
)
from tests.integration.providers.k8s.conftest import (
    make_deployment_object,
    make_kubernetes_client_mock,
    make_pod_object,
    make_request,
    make_strategy,
    make_template,
)


def _build_deployment_request(*, namespace: str, requested_count: int) -> Any:
    template = make_template(provider_api="Deployment", namespace=namespace)
    return make_request(
        provider_api="Deployment",
        requested_count=requested_count,
        namespace=namespace,
        template=template,
    )


@pytest.mark.asyncio
async def test_deployment_full_lifecycle_acquire_status_selective_release() -> None:
    """Acquire -> watch pods come up -> selectively release two of three."""
    namespace = "orb-it"

    # Track the Deployment body submitted at acquire time so we can
    # assert label propagation on the pod template.
    created_deployments: list[Any] = []

    def _create_deployment(*, namespace: str, body: Any) -> Any:
        created_deployments.append(body)
        return SimpleNamespace()

    apps_v1 = MagicMock()
    apps_v1.create_namespaced_deployment.side_effect = _create_deployment
    core_v1 = MagicMock()
    client = make_kubernetes_client_mock(core_v1=core_v1, apps_v1=apps_v1)
    strategy = make_strategy(client=client)

    request = _build_deployment_request(namespace=namespace, requested_count=3)
    request_label = str(request.request_id)

    # ---- acquire ----------------------------------------------------------
    outcome = await strategy.acquire(request)
    assert isinstance(outcome, Accepted)
    # Deployment handler returns the workload name as the sole resource id.
    assert len(outcome.pending_resource_ids) == 1
    deployment_name = outcome.pending_resource_ids[0]
    assert deployment_name.startswith("orb-")

    # Label propagation on Deployment metadata AND on the pod template
    # selector.  The controller picks pods via the spec.selector match
    # labels which must include the request-id label.
    assert apps_v1.create_namespaced_deployment.call_count == 1
    body = created_deployments[0]
    assert body.metadata.labels["orb.io/managed"] == "true"
    assert body.metadata.labels["orb.io/request-id"] == request_label
    assert body.metadata.labels["orb.io/provider-api"] == "Deployment"
    pod_template_labels = body.spec.template.metadata.labels
    assert pod_template_labels["orb.io/request-id"] == request_label
    assert body.spec.replicas == 3
    selector_labels = body.spec.selector.match_labels
    assert selector_labels["orb.io/request-id"] == request_label

    # ---- get_status (pending -> running) ---------------------------------
    pod_names = [f"{deployment_name}-pod-{i}" for i in range(3)]
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            make_pod_object(
                name=name,
                namespace=namespace,
                request_id=request_label,
                phase="Pending",
                ready=False,
            )
            for name in pod_names
        ]
    )
    apps_v1.read_namespaced_deployment.return_value = make_deployment_object(
        name=deployment_name,
        namespace=namespace,
        spec_replicas=3,
        ready_replicas=0,
    )

    pending_outcome = await strategy.get_status([deployment_name], request)
    assert isinstance(pending_outcome, Accepted)
    assert pending_outcome.metadata["fulfilment"].state == "in_progress"

    # Now flip to ready=True on every pod and ready_replicas=3 on the
    # Deployment.  The handler should report ``fulfilled``.
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            make_pod_object(
                name=name,
                namespace=namespace,
                request_id=request_label,
                phase="Running",
                ready=True,
            )
            for name in pod_names
        ]
    )
    apps_v1.read_namespaced_deployment.return_value = make_deployment_object(
        name=deployment_name,
        namespace=namespace,
        spec_replicas=3,
        ready_replicas=3,
        available_replicas=3,
    )
    ready_outcome = await strategy.get_status([deployment_name], request)
    assert isinstance(ready_outcome, Completed)
    assert ready_outcome.metadata["fulfilment"].state == "fulfilled"
    assert ready_outcome.metadata["fulfilment"].running_count == 3

    # ---- release (selective) ---------------------------------------------
    # The handler MUST: annotate the victim pods with the
    # pod-deletion-cost, then patch spec.replicas down by the victim
    # count.  It MUST NOT call ``delete_namespaced_pod`` directly.
    apps_v1.read_namespaced_deployment.return_value = make_deployment_object(
        name=deployment_name,
        namespace=namespace,
        spec_replicas=3,
        ready_replicas=3,
    )
    apps_v1.patch_namespaced_deployment_scale.return_value = SimpleNamespace()
    core_v1.patch_namespaced_pod.return_value = SimpleNamespace()

    # Carry the deployment name on the request's provider_data the same
    # way the production code does, so the release path can resolve the
    # controller name from the request.
    request_with_workload = make_request(
        request_id=str(request.request_id),
        provider_api="Deployment",
        requested_count=3,
        namespace=namespace,
        extra_provider_data={"deployment_name": deployment_name},
        template=make_template(provider_api="Deployment", namespace=namespace),
    )

    victims = pod_names[:2]
    release_outcome = await strategy.return_machines(victims, request_with_workload)
    assert isinstance(release_outcome, Accepted)

    # Two annotate patches, one scale patch, NO pod deletes.
    assert core_v1.patch_namespaced_pod.call_count == 2
    for call in core_v1.patch_namespaced_pod.call_args_list:
        body = call.kwargs["body"]
        assert body["metadata"]["annotations"][POD_DELETION_COST_ANNOTATION] == (
            VICTIM_DELETION_COST
        )
    assert apps_v1.patch_namespaced_deployment_scale.call_count == 1
    scale_body = apps_v1.patch_namespaced_deployment_scale.call_args.kwargs["body"]
    assert scale_body["spec"]["replicas"] == 1  # 3 - 2
    assert core_v1.delete_namespaced_pod.call_count == 0


@pytest.mark.asyncio
async def test_deployment_full_release_deletes_workload() -> None:
    """Releasing every replica scales to zero and deletes the Deployment."""
    namespace = "orb-it"

    apps_v1 = MagicMock()
    apps_v1.create_namespaced_deployment.return_value = SimpleNamespace()
    core_v1 = MagicMock()
    client = make_kubernetes_client_mock(core_v1=core_v1, apps_v1=apps_v1)
    strategy = make_strategy(client=client)

    request = _build_deployment_request(namespace=namespace, requested_count=2)
    acquire_outcome = await strategy.acquire(request)
    assert isinstance(acquire_outcome, Accepted)
    deployment_name = acquire_outcome.pending_resource_ids[0]

    # Full release: victims cover every replica.  Read returns
    # spec.replicas=2 and the patch / delete calls fire in order.
    apps_v1.read_namespaced_deployment.return_value = make_deployment_object(
        name=deployment_name,
        namespace=namespace,
        spec_replicas=2,
        ready_replicas=2,
    )
    apps_v1.patch_namespaced_deployment_scale.return_value = SimpleNamespace()
    apps_v1.delete_namespaced_deployment.return_value = SimpleNamespace()

    request_with_workload = make_request(
        request_id=str(request.request_id),
        provider_api="Deployment",
        requested_count=2,
        namespace=namespace,
        extra_provider_data={"deployment_name": deployment_name},
        template=make_template(provider_api="Deployment", namespace=namespace),
    )

    release_outcome = await strategy.return_machines(
        [f"{deployment_name}-pod-0", f"{deployment_name}-pod-1"],
        request_with_workload,
    )
    assert isinstance(release_outcome, Accepted)
    # Scale to zero, then delete.
    scale_call = apps_v1.patch_namespaced_deployment_scale.call_args
    assert scale_call.kwargs["body"]["spec"]["replicas"] == 0
    assert apps_v1.delete_namespaced_deployment.call_count == 1
    # Never annotate victims for a full release.
    assert core_v1.patch_namespaced_pod.call_count == 0
