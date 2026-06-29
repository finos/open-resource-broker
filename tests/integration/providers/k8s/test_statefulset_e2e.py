"""End-to-end integration test for the ``StatefulSet`` API.

Drives the strategy + StatefulSet handler through:

* ``acquire`` — a single StatefulSet with ``spec.replicas=N``;
* ``get_status`` — pod list cross-referenced with StatefulSet status
  (``ready_replicas`` is authoritative);
* ``return_machines`` (selective) — patch ``spec.replicas`` to
  ``current - len(victims)`` without ever deleting pods directly.  Logs
  a warning when the requested victims are not the top-of-stack
  ordinals;
* ``return_machines`` (full release) — scale-to-zero and delete.

Asserts label propagation, request_id linkage, and that the controller-
driven scale-down path is used everywhere.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.base.operation_outcome import Accepted, Completed
from tests.integration.providers.k8s.conftest import (
    make_kubernetes_client_mock,
    make_pod_object,
    make_request,
    make_statefulset_object,
    make_strategy,
    make_template,
)


def _build_sts_request(*, namespace: str, requested_count: int) -> Any:
    template = make_template(provider_api="StatefulSet", namespace=namespace)
    return make_request(
        provider_api="StatefulSet",
        requested_count=requested_count,
        namespace=namespace,
        template=template,
    )


@pytest.mark.asyncio
async def test_statefulset_full_lifecycle_acquire_status_selective_release() -> None:
    """Acquire 3 replicas, observe rolling readiness, then scale down by one."""
    namespace = "orb-it"

    created_statefulsets: list[Any] = []

    def _create_sts(*, namespace: str, body: Any) -> Any:
        created_statefulsets.append(body)
        return SimpleNamespace()

    apps_v1 = MagicMock()
    apps_v1.create_namespaced_stateful_set.side_effect = _create_sts
    core_v1 = MagicMock()
    client = make_kubernetes_client_mock(core_v1=core_v1, apps_v1=apps_v1)
    strategy = make_strategy(client=client)

    request = _build_sts_request(namespace=namespace, requested_count=3)
    request_label = str(request.request_id)

    # ---- acquire ----------------------------------------------------------
    outcome = await strategy.acquire(request)
    assert isinstance(outcome, Accepted)
    statefulset_name = outcome.pending_resource_ids[0]
    assert statefulset_name.startswith("orb-")

    body = created_statefulsets[0]
    assert body.spec.replicas == 3
    assert body.metadata.labels["orb.io/managed"] == "true"
    assert body.metadata.labels["orb.io/request-id"] == request_label
    assert body.metadata.labels["orb.io/provider-api"] == "StatefulSet"
    # Pod template + selector both carry the request-id label so the
    # controller picks up the right pod set on watch reconciliation.
    pod_template_labels = body.spec.template.metadata.labels
    assert pod_template_labels["orb.io/request-id"] == request_label
    selector_labels = body.spec.selector.match_labels
    assert selector_labels["orb.io/request-id"] == request_label

    # ---- get_status (rolling ready) --------------------------------------
    # StatefulSets ramp up in order; first two are ready, third is
    # pending.  The controller's view should mark this as in_progress.
    pod_names = [f"{statefulset_name}-{i}" for i in range(3)]
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            make_pod_object(
                name=pod_names[0],
                namespace=namespace,
                request_id=request_label,
                phase="Running",
                ready=True,
            ),
            make_pod_object(
                name=pod_names[1],
                namespace=namespace,
                request_id=request_label,
                phase="Running",
                ready=True,
            ),
            make_pod_object(
                name=pod_names[2],
                namespace=namespace,
                request_id=request_label,
                phase="Pending",
                ready=False,
            ),
        ]
    )
    apps_v1.read_namespaced_stateful_set.return_value = make_statefulset_object(
        name=statefulset_name,
        namespace=namespace,
        spec_replicas=3,
        ready_replicas=2,
        current_replicas=3,
    )
    rolling_outcome = await strategy.get_status([statefulset_name], request)
    assert isinstance(rolling_outcome, Accepted)
    assert rolling_outcome.metadata["fulfilment"].state == "in_progress"

    # Now flip the third pod to Ready and ready_replicas=3.
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
    apps_v1.read_namespaced_stateful_set.return_value = make_statefulset_object(
        name=statefulset_name,
        namespace=namespace,
        spec_replicas=3,
        ready_replicas=3,
        current_replicas=3,
    )
    ready_outcome = await strategy.get_status([statefulset_name], request)
    assert isinstance(ready_outcome, Completed)
    assert ready_outcome.metadata["fulfilment"].state == "fulfilled"

    # ---- release (selective) ---------------------------------------------
    apps_v1.read_namespaced_stateful_set.return_value = make_statefulset_object(
        name=statefulset_name,
        namespace=namespace,
        spec_replicas=3,
        ready_replicas=3,
    )
    apps_v1.patch_namespaced_stateful_set_scale.return_value = SimpleNamespace()

    request_with_workload = make_request(
        request_id=str(request.request_id),
        provider_api="StatefulSet",
        requested_count=3,
        namespace=namespace,
        extra_provider_data={"statefulset_name": statefulset_name},
        template=make_template(provider_api="StatefulSet", namespace=namespace),
    )

    # Release the highest-ordinal pod (top-of-stack).  This must NOT
    # trigger any direct pod deletes — the StatefulSet controller owns
    # eviction order.
    release_outcome = await strategy.return_machines([pod_names[-1]], request_with_workload)
    assert isinstance(release_outcome, Accepted)
    scale_body = apps_v1.patch_namespaced_stateful_set_scale.call_args.kwargs["body"]
    assert scale_body["spec"]["replicas"] == 2  # 3 - 1
    assert core_v1.delete_namespaced_pod.call_count == 0


@pytest.mark.asyncio
async def test_statefulset_full_release_deletes_workload() -> None:
    """Releasing every replica scales to zero and deletes the StatefulSet."""
    namespace = "orb-it"

    apps_v1 = MagicMock()
    apps_v1.create_namespaced_stateful_set.return_value = SimpleNamespace()
    core_v1 = MagicMock()
    client = make_kubernetes_client_mock(core_v1=core_v1, apps_v1=apps_v1)
    strategy = make_strategy(client=client)

    request = _build_sts_request(namespace=namespace, requested_count=2)
    acquire_outcome = await strategy.acquire(request)
    assert isinstance(acquire_outcome, Accepted)
    statefulset_name = acquire_outcome.pending_resource_ids[0]

    apps_v1.read_namespaced_stateful_set.return_value = make_statefulset_object(
        name=statefulset_name,
        namespace=namespace,
        spec_replicas=2,
        ready_replicas=2,
    )
    apps_v1.patch_namespaced_stateful_set_scale.return_value = SimpleNamespace()
    apps_v1.delete_namespaced_stateful_set.return_value = SimpleNamespace()

    request_with_workload = make_request(
        request_id=str(request.request_id),
        provider_api="StatefulSet",
        requested_count=2,
        namespace=namespace,
        extra_provider_data={"statefulset_name": statefulset_name},
        template=make_template(provider_api="StatefulSet", namespace=namespace),
    )

    release_outcome = await strategy.return_machines(
        [f"{statefulset_name}-0", f"{statefulset_name}-1"],
        request_with_workload,
    )
    assert isinstance(release_outcome, Accepted)
    assert (
        apps_v1.patch_namespaced_stateful_set_scale.call_args.kwargs["body"]["spec"]["replicas"]
        == 0
    )
    assert apps_v1.delete_namespaced_stateful_set.call_count == 1
    assert core_v1.delete_namespaced_pod.call_count == 0
