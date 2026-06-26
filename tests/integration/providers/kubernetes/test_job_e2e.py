"""End-to-end integration test for the ``KubernetesJob`` API.

Drives the strategy + Job handler through:

* ``acquire`` — single Job with ``parallelism = completions = N`` and
  ``backoffLimit=0`` (set by the spec builder);
* ``get_status`` — active pods first, then the Job's ``Complete``
  condition flipping to True (controller view wins);
* ``return_machines`` — entire Job is deleted with
  ``propagationPolicy=Background``.  Selective release is not supported
  for Jobs and must NOT be attempted on individual pods.

Asserts label propagation, request_id linkage on the Job and pod
template, and that the terminal Job condition produces a ``Completed``
``fulfilled`` outcome.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.base.operation_outcome import Accepted, Completed
from tests.integration.providers.kubernetes.conftest import (
    make_job_object,
    make_kubernetes_client_mock,
    make_pod_object,
    make_request,
    make_strategy,
    make_template,
)


def _build_job_request(*, namespace: str, requested_count: int) -> Any:
    template = make_template(provider_api="KubernetesJob", namespace=namespace)
    return make_request(
        provider_api="KubernetesJob",
        requested_count=requested_count,
        namespace=namespace,
        template=template,
    )


@pytest.mark.asyncio
async def test_job_full_lifecycle_acquire_status_release() -> None:
    """Acquire a Job, observe active -> complete, then delete the Job."""
    namespace = "orb-it"

    created_jobs: list[Any] = []

    def _create_job(*, namespace: str, body: Any) -> Any:
        created_jobs.append(body)
        return SimpleNamespace()

    batch_v1 = MagicMock()
    batch_v1.create_namespaced_job.side_effect = _create_job
    core_v1 = MagicMock()
    client = make_kubernetes_client_mock(core_v1=core_v1, batch_v1=batch_v1)
    strategy = make_strategy(client=client)

    request = _build_job_request(namespace=namespace, requested_count=2)
    request_label = str(request.request_id)

    # ---- acquire ----------------------------------------------------------
    outcome = await strategy.acquire(request)
    assert isinstance(outcome, Accepted)
    job_name = outcome.pending_resource_ids[0]
    assert job_name.startswith("orb-")

    body = created_jobs[0]
    assert body.spec.parallelism == 2
    assert body.spec.completions == 2
    # Job invariant: ORB sets backoff_limit=0 so a pod failure surfaces
    # immediately rather than being retried by the Job controller.
    assert body.spec.backoff_limit == 0
    assert body.metadata.labels["orb.io/managed"] == "true"
    assert body.metadata.labels["orb.io/request-id"] == request_label
    assert body.metadata.labels["orb.io/provider-api"] == "KubernetesJob"
    pod_template_labels = body.spec.template.metadata.labels
    assert pod_template_labels["orb.io/request-id"] == request_label
    selector_labels = body.spec.selector.match_labels
    assert selector_labels["orb.io/request-id"] == request_label

    # ---- get_status (active) ---------------------------------------------
    pod_names = [f"{job_name}-pod-{i}" for i in range(2)]
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
    batch_v1.read_namespaced_job.return_value = make_job_object(
        name=job_name,
        namespace=namespace,
        parallelism=2,
        active=2,
        succeeded=0,
        failed=0,
    )
    active_outcome = await strategy.get_status([job_name], request)
    # Active and not yet complete: in_progress (handler treats active>0
    # as pending until succeeded count or Complete condition catches up).
    assert isinstance(active_outcome, Accepted)
    assert active_outcome.metadata["fulfilment"].state == "in_progress"

    # ---- get_status (complete) -------------------------------------------
    # All pods succeeded; Job's Complete condition is True.
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            make_pod_object(
                name=name,
                namespace=namespace,
                request_id=request_label,
                phase="Succeeded",
                ready=False,
            )
            for name in pod_names
        ]
    )
    batch_v1.read_namespaced_job.return_value = make_job_object(
        name=job_name,
        namespace=namespace,
        parallelism=2,
        active=0,
        succeeded=2,
        failed=0,
        completion_conditions=[("Complete", "True")],
    )
    complete_outcome = await strategy.get_status([job_name], request)
    assert isinstance(complete_outcome, Completed)
    assert complete_outcome.metadata["fulfilment"].state == "fulfilled"
    # The Job's succeeded count is authoritative for the running tally.
    assert complete_outcome.metadata["fulfilment"].running_count == 2

    # ---- release ----------------------------------------------------------
    batch_v1.delete_namespaced_job.return_value = SimpleNamespace()
    request_with_workload = make_request(
        request_id=str(request.request_id),
        provider_api="KubernetesJob",
        requested_count=2,
        namespace=namespace,
        extra_provider_data={"job_name": job_name},
        template=make_template(provider_api="KubernetesJob", namespace=namespace),
    )
    release_outcome = await strategy.return_machines(pod_names, request_with_workload)
    assert isinstance(release_outcome, Accepted)
    assert batch_v1.delete_namespaced_job.call_count == 1
    delete_kwargs = batch_v1.delete_namespaced_job.call_args.kwargs
    assert delete_kwargs["name"] == job_name
    # Background propagation lets the controller clean up pods async.
    assert delete_kwargs["propagation_policy"] == "Background"
    # Selective release is NOT supported — no per-pod delete attempts.
    assert core_v1.delete_namespaced_pod.call_count == 0


@pytest.mark.asyncio
async def test_job_failed_condition_surfaces_failed_fulfilment() -> None:
    """A Job that reports ``Failed`` condition surfaces as terminal failure."""
    namespace = "orb-it"

    batch_v1 = MagicMock()
    batch_v1.create_namespaced_job.return_value = SimpleNamespace()
    core_v1 = MagicMock()
    client = make_kubernetes_client_mock(core_v1=core_v1, batch_v1=batch_v1)
    strategy = make_strategy(client=client)

    request = _build_job_request(namespace=namespace, requested_count=1)
    acquire_outcome = await strategy.acquire(request)
    assert isinstance(acquire_outcome, Accepted)
    job_name = acquire_outcome.pending_resource_ids[0]
    request_label = str(request.request_id)

    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            make_pod_object(
                name=f"{job_name}-pod-0",
                namespace=namespace,
                request_id=request_label,
                phase="Failed",
                ready=False,
                container_reason="Error",
            )
        ]
    )
    batch_v1.read_namespaced_job.return_value = make_job_object(
        name=job_name,
        namespace=namespace,
        parallelism=1,
        active=0,
        succeeded=0,
        failed=1,
        completion_conditions=[("Failed", "True")],
    )
    outcome = await strategy.get_status([job_name], request)
    assert isinstance(outcome, Completed)
    assert outcome.metadata["fulfilment"].state == "failed"
