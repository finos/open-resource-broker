"""End-to-end integration test for the ``KubernetesPod`` provider API.

Drives a full ``acquire -> get_status -> get_status -> release`` flow
through :class:`KubernetesProviderStrategy`, exercising the Pod handler
with a mocked ``CoreV1Api``.  Each phase asserts:

* labels stamped on every pod create call (``managed``,
  ``request-id``, ``machine-id``, ``provider-type`` and
  ``provider-api``);
* :class:`OperationOutcome` shape returned by the strategy
  (``Accepted`` on acquire, ``Accepted`` while pods are pending,
  ``Completed`` once fulfilment is terminal);
* request-id linkage on the per-instance dicts surfaced by
  ``get_status`` and on the label selector handed to
  ``list_namespaced_pod``;
* status transitions: cluster reports Pending first, then Running, then
  the strategy ``return_machines`` deletes the pods by name.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.base.operation_outcome import Accepted, Completed
from tests.integration.providers.kubernetes.conftest import (
    make_kubernetes_client_mock,
    make_pod_object,
    make_request,
    make_strategy,
    make_template,
)


def _build_pod_request(*, namespace: str, requested_count: int) -> Any:
    template = make_template(provider_api="KubernetesPod", namespace=namespace)
    return make_request(
        provider_api="KubernetesPod",
        requested_count=requested_count,
        namespace=namespace,
        template=template,
    )


@pytest.mark.asyncio
async def test_pod_full_lifecycle_acquire_status_release() -> None:
    """Acquire 2 pods, observe Pending -> Running, then release them."""
    namespace = "orb-it"

    # Track which pods the test layer has "created" so the strategy
    # ``acquire`` populates the cluster view used by subsequent list
    # calls.  The Pod handler hands a ``V1Pod`` body to
    # ``create_namespaced_pod``; we capture each body so we can assert
    # the labels later.
    created_bodies: list[Any] = []

    def _create_pod(*, namespace: str, body: Any) -> Any:
        created_bodies.append(body)
        return SimpleNamespace()

    core_v1 = MagicMock()
    core_v1.create_namespaced_pod.side_effect = _create_pod

    client = make_kubernetes_client_mock(core_v1=core_v1)
    strategy = make_strategy(client=client)

    request = _build_pod_request(namespace=namespace, requested_count=2)

    # ---- acquire ----------------------------------------------------------
    outcome = await strategy.acquire(request)
    assert isinstance(outcome, Accepted)
    assert outcome.request_id == str(request.request_id)
    assert len(outcome.pending_resource_ids) == 2
    pod_names = sorted(outcome.pending_resource_ids)

    # Label propagation — every created pod carries the canonical ORB
    # label set keyed by request_id / machine_id / provider-type.
    assert core_v1.create_namespaced_pod.call_count == 2
    expected_request_label = str(request.request_id)
    for body in created_bodies:
        labels = body.metadata.labels
        assert labels["orb.io/managed"] == "true"
        assert labels["orb.io/request-id"] == expected_request_label
        assert labels["orb.io/provider-api"] == "KubernetesPod"
        assert labels["orb.io/machine-id"] == body.metadata.name
        assert labels["orb.io/template-id"] == str(request.template_id)

    # ---- get_status (pending) --------------------------------------------
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            make_pod_object(
                name=name,
                namespace=namespace,
                request_id=expected_request_label,
                phase="Pending",
                ready=False,
            )
            for name in pod_names
        ]
    )
    status_outcome = await strategy.get_status(pod_names, request)
    assert isinstance(status_outcome, Accepted)
    assert status_outcome.metadata["fulfilment"].state == "in_progress"

    # The list call must be scoped to the request via the request-id selector.
    list_kwargs = core_v1.list_namespaced_pod.call_args.kwargs
    assert list_kwargs["namespace"] == namespace
    assert f"orb.io/request-id={expected_request_label}" in list_kwargs["label_selector"]

    # ---- get_status (running) --------------------------------------------
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            make_pod_object(
                name=name,
                namespace=namespace,
                request_id=expected_request_label,
                phase="Running",
                ready=True,
            )
            for name in pod_names
        ]
    )
    status_outcome = await strategy.get_status(pod_names, request)
    assert isinstance(status_outcome, Completed)
    assert sorted(status_outcome.resource_ids) == pod_names
    assert status_outcome.metadata["fulfilment"].state == "fulfilled"
    # Every instance reports back its provider_api and request linkage.
    for inst in status_outcome.metadata["instances"]:
        assert inst["provider_api"] == "KubernetesPod"
        assert inst["tags"]["orb.io/request-id"] == expected_request_label
        assert inst["status"] == "running"

    # ---- release ----------------------------------------------------------
    core_v1.delete_namespaced_pod.return_value = SimpleNamespace()
    release_outcome = await strategy.return_machines(pod_names, request)
    assert isinstance(release_outcome, Accepted)
    assert sorted(release_outcome.pending_resource_ids) == pod_names
    assert core_v1.delete_namespaced_pod.call_count == 2

    # The delete calls target the right namespace and pod names.
    deleted_names = sorted(
        call.kwargs["name"] for call in core_v1.delete_namespaced_pod.call_args_list
    )
    assert deleted_names == pod_names


@pytest.mark.asyncio
async def test_pod_status_failure_surfaces_as_completed_failed() -> None:
    """A request whose pods all fail surfaces as a terminal ``Completed``.

    ``Completed`` is the strategy's terminal verdict; the per-instance
    statuses encode the failure so the caller can inspect them.
    """
    namespace = "orb-it"
    core_v1 = MagicMock()
    core_v1.create_namespaced_pod.return_value = SimpleNamespace()
    client = make_kubernetes_client_mock(core_v1=core_v1)
    strategy = make_strategy(client=client)

    request = _build_pod_request(namespace=namespace, requested_count=2)
    outcome = await strategy.acquire(request)
    assert isinstance(outcome, Accepted)
    pod_names = sorted(outcome.pending_resource_ids)

    expected_request_label = str(request.request_id)
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            make_pod_object(
                name=pod_names[0],
                namespace=namespace,
                request_id=expected_request_label,
                phase="Failed",
                ready=False,
                container_reason="OOMKilled",
            ),
            make_pod_object(
                name=pod_names[1],
                namespace=namespace,
                request_id=expected_request_label,
                phase="Failed",
                ready=False,
                container_reason="Error",
            ),
        ]
    )

    status_outcome = await strategy.get_status(pod_names, request)
    assert isinstance(status_outcome, Completed)
    assert status_outcome.metadata["fulfilment"].state == "failed"
    statuses = [inst["status"] for inst in status_outcome.metadata["instances"]]
    assert statuses == ["failed", "failed"]
    reasons = {inst["status_reason"] for inst in status_outcome.metadata["instances"]}
    assert reasons == {"OOMKilled", "Error"}


@pytest.mark.asyncio
async def test_pod_release_swallows_already_gone_404() -> None:
    """Release path treats 404 as best-effort so retries do not error out."""
    from kubernetes.client.exceptions import ApiException

    namespace = "orb-it"

    core_v1 = MagicMock()
    core_v1.create_namespaced_pod.return_value = SimpleNamespace()
    client = make_kubernetes_client_mock(core_v1=core_v1)
    strategy = make_strategy(client=client)

    request = _build_pod_request(namespace=namespace, requested_count=1)
    acquire_outcome = await strategy.acquire(request)
    assert isinstance(acquire_outcome, Accepted)
    pod_names = acquire_outcome.pending_resource_ids

    def _delete(*, name: str, namespace: str) -> None:
        raise ApiException(status=404, reason="Not Found")

    core_v1.delete_namespaced_pod.side_effect = _delete

    # Must NOT raise — the 404 short-circuits the retry path.
    release_outcome = await strategy.return_machines(pod_names, request)
    assert isinstance(release_outcome, Accepted)
