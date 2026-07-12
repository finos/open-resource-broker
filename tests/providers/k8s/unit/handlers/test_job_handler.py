"""Unit tests for :class:`K8sJobHandler`.

Mocks ``CoreV1Api`` and ``BatchV1Api`` so no cluster is required.
Covers:

* ``acquire_hosts`` creates a single Job with
  ``parallelism = completions = N`` and ``backoffLimit = 0``, and
  persists the job name in ``provider_data``.
* ``release_hosts`` deletes the whole Job with background propagation
  (the Job controller cascade-deletes pods).  Selective release is not
  supported — any ``machine_ids`` list triggers a full delete.
* ``check_hosts_status`` reads both the pod list and the Job status,
  rolling up via the Job's ``succeeded`` / ``Complete`` condition.
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
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    requested_count: int = 3,
    request_id: str | None = None,
    job_name: str | None = None,
    namespace: str = "orb-test",
    parallelism: int | None = None,
) -> Request:
    provider_data: dict[str, Any] = {"namespace": namespace}
    if job_name:
        provider_data["job_name"] = job_name
    # Mirror the shape written by acquire_hosts: always include parallelism so
    # the release guard can confirm full-vs-partial release.
    provider_data["parallelism"] = parallelism if parallelism is not None else requested_count
    return Request(
        request_id=RequestId(value=request_id or f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Job",
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data=provider_data,
    )


def _make_template() -> Template:
    return Template(
        template_id="tpl-1",
        provider_type="k8s",
        provider_api="Job",
        image_id="busybox:latest",
        max_instances=5,
        provider_data={
            "k8s": {
                "namespace": "orb-test",
                "container_image": "busybox:latest",
                "resource_requests": {"cpu": "100m", "memory": "64Mi"},
                "command": ["sh", "-c", "exit 0"],
            }
        },
    )


def _make_client(
    core_v1: Any | None = None,
    batch_v1: Any | None = None,
) -> MagicMock:
    client = MagicMock()
    client.core_v1 = core_v1 if core_v1 is not None else MagicMock()
    client.batch_v1 = batch_v1 if batch_v1 is not None else MagicMock()
    return client


def _make_handler(client: Any | None = None) -> K8sJobHandler:
    if client is None:
        client = _make_client()
    config = K8sProviderConfig(namespace="orb-test")
    return K8sJobHandler(
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


def _make_job_status(
    *,
    active: int | None = None,
    succeeded: int | None = None,
    failed: int | None = None,
    conditions: list[dict[str, Any]] | None = None,
) -> SimpleNamespace:
    condition_objects = [
        SimpleNamespace(
            type=c.get("type"),
            status=c.get("status"),
            reason=c.get("reason"),
            message=c.get("message"),
        )
        for c in (conditions or [])
    ]
    return SimpleNamespace(
        metadata=SimpleNamespace(name="orb-deadbeef", namespace="orb-test"),
        spec=SimpleNamespace(parallelism=3, completions=3, backoff_limit=0),
        status=SimpleNamespace(
            active=active,
            succeeded=succeeded,
            failed=failed,
            conditions=condition_objects,
        ),
    )


# ---------------------------------------------------------------------------
# acquire_hosts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_hosts_creates_single_job_with_parallelism_and_completions() -> None:
    batch_v1 = MagicMock()
    batch_v1.create_namespaced_job.return_value = SimpleNamespace()
    client = _make_client(batch_v1=batch_v1)
    handler = _make_handler(client=client)

    request = _make_request(requested_count=4)
    template = _make_template()

    result = await handler.acquire_hosts(request, template)

    # Exactly one Job created.
    assert batch_v1.create_namespaced_job.call_count == 1
    call_kwargs = batch_v1.create_namespaced_job.call_args.kwargs
    assert call_kwargs["namespace"] == "orb-test"
    body = call_kwargs["body"]
    # parallelism == completions == requested_count.
    assert body.spec.parallelism == 4
    assert body.spec.completions == 4
    # backoffLimit must be zero — ORB owns retry.
    assert body.spec.backoff_limit == 0
    # ``manual_selector=True`` lets the API server accept our explicit
    # selector (otherwise it would auto-generate controller-uid /
    # job-name labels).
    assert body.spec.manual_selector is True
    # restartPolicy must be Never (required by backoffLimit=0).
    assert body.spec.template.spec.restart_policy == "Never"
    # resource_ids carries the Job name; machine_ids stays empty
    # because the controller stamps pod names asynchronously.
    assert len(result["resource_ids"]) == 1
    assert result["resource_ids"][0].startswith("orb-")
    assert result["machine_ids"] == []
    assert result["provider_data"]["parallelism"] == 4
    assert result["provider_data"]["namespace"] == "orb-test"
    assert result["provider_data"]["job_name"] == result["resource_ids"][0]


@pytest.mark.asyncio
async def test_acquire_hosts_parallelism_floors_at_one() -> None:
    batch_v1 = MagicMock()
    batch_v1.create_namespaced_job.return_value = SimpleNamespace()
    client = _make_client(batch_v1=batch_v1)
    handler = _make_handler(client=client)

    request = _make_request(requested_count=0)
    template = _make_template()

    await handler.acquire_hosts(request, template)
    body = batch_v1.create_namespaced_job.call_args.kwargs["body"]
    # parallelism must be >= 1 — the Job API rejects 0 anyway.
    assert body.spec.parallelism == 1
    assert body.spec.completions == 1


# ---------------------------------------------------------------------------
# release_hosts — always full delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_hosts_deletes_whole_job_with_background_propagation() -> None:
    """Full release (machine_ids == parallelism) deletes the Job with background propagation."""
    batch_v1 = MagicMock()
    batch_v1.delete_namespaced_job.return_value = SimpleNamespace()
    client = _make_client(batch_v1=batch_v1)
    handler = _make_handler(client=client)

    # Full release: 3 machine_ids match parallelism=3.
    request = _make_request(
        requested_count=3,
        job_name="orb-deadbeef",
        namespace="orb-test",
        parallelism=3,
    )

    await handler.release_hosts(
        ["orb-deadbeef-pod1", "orb-deadbeef-pod2", "orb-deadbeef-pod3"],
        request.provider_data,
    )

    batch_v1.delete_namespaced_job.assert_called_once()
    kwargs = batch_v1.delete_namespaced_job.call_args.kwargs
    assert kwargs["name"] == "orb-deadbeef"
    assert kwargs["namespace"] == "orb-test"
    assert kwargs["propagation_policy"] == "Background"


@pytest.mark.asyncio
async def test_release_hosts_full_release_lists_all_machine_ids() -> None:
    """Passing all machine_ids (matching parallelism) triggers whole-Job delete."""
    from orb.providers.k8s.exceptions.k8s_exceptions import K8sError

    batch_v1 = MagicMock()
    batch_v1.delete_namespaced_job.return_value = SimpleNamespace()
    client = _make_client(batch_v1=batch_v1)
    handler = _make_handler(client=client)

    request = _make_request(
        requested_count=3,
        job_name="orb-deadbeef",
        namespace="orb-test",
        parallelism=3,
    )

    await handler.release_hosts(["pod-a", "pod-b", "pod-c"], request.provider_data)

    # Whole-Job delete; the controller cascade-deletes the pods.
    batch_v1.delete_namespaced_job.assert_called_once()

    # Partial release (only 1 of 3) must now be refused.
    request_partial = _make_request(
        requested_count=3,
        job_name="orb-deadbeef",
        namespace="orb-test",
        parallelism=3,
    )
    with pytest.raises(K8sError, match="selective release refused"):
        await handler.release_hosts(["pod-a"], request_partial.provider_data)
    # The delete must NOT have been called for the partial case.
    assert batch_v1.delete_namespaced_job.call_count == 1  # only from the full release above


@pytest.mark.asyncio
async def test_release_hosts_empty_machine_ids_is_noop() -> None:
    batch_v1 = MagicMock()
    client = _make_client(batch_v1=batch_v1)
    handler = _make_handler(client=client)

    request = _make_request()
    await handler.release_hosts([], request.provider_data)

    batch_v1.delete_namespaced_job.assert_not_called()


@pytest.mark.asyncio
async def test_release_hosts_job_already_gone_is_best_effort() -> None:
    """Full release of an already-gone Job is a no-op (404 is best-effort)."""
    from kubernetes.client.exceptions import ApiException

    batch_v1 = MagicMock()
    batch_v1.delete_namespaced_job.side_effect = ApiException(status=404, reason="Not Found")

    client = _make_client(batch_v1=batch_v1)
    handler = _make_handler(client=client)
    handler._max_retries = 1

    # Must use parallelism=1 so machine_ids=["pod-x"] counts as a full release.
    request = _make_request(job_name="orb-deadbeef", requested_count=1, parallelism=1)
    # Must not raise — Job evaporated, treat as success.
    await handler.release_hosts(["pod-x"], request.provider_data)


# ---------------------------------------------------------------------------
# check_hosts_status
# ---------------------------------------------------------------------------


def test_check_hosts_status_fulfilled_when_complete_condition_true() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-deadbeef-aaaaa", phase="Succeeded"),
            _make_pod(name="orb-deadbeef-bbbbb", phase="Succeeded"),
        ]
    )
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.return_value = _make_job_status(
        active=0,
        succeeded=2,
        failed=0,
        conditions=[{"type": "Complete", "status": "True", "reason": "JobComplete"}],
    )

    client = _make_client(core_v1=core_v1, batch_v1=batch_v1)
    handler = _make_handler(client=client)
    request = _make_request(requested_count=2, job_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    assert isinstance(result, CheckHostsStatusResult)
    assert isinstance(result.fulfilment, ProviderFulfilment)
    assert result.fulfilment.state == "fulfilled"
    assert result.fulfilment.target_units == 2
    assert result.fulfilment.running_count == 2


def test_check_hosts_status_failed_when_failed_condition_true() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-deadbeef-fff01", phase="Failed"),
        ]
    )
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.return_value = _make_job_status(
        active=0,
        succeeded=0,
        failed=1,
        conditions=[{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}],
    )

    client = _make_client(core_v1=core_v1, batch_v1=batch_v1)
    handler = _make_handler(client=client)
    request = _make_request(requested_count=2, job_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "failed"
    assert result.fulfilment.failed_count == 1


def test_check_hosts_status_in_progress_with_active_pods() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-deadbeef-a", phase="Running", ready=True),
            _make_pod(name="orb-deadbeef-b", phase="Pending"),
        ]
    )
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.return_value = _make_job_status(
        active=2,
        succeeded=0,
        failed=0,
    )

    client = _make_client(core_v1=core_v1, batch_v1=batch_v1)
    handler = _make_handler(client=client)
    request = _make_request(requested_count=2, job_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "in_progress"


def test_check_hosts_status_partial_after_some_pods_succeeded() -> None:
    """When some pods have succeeded but the Job isn't ``Complete`` yet
    (and there are no active pods), the verdict should be ``partial``."""
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-deadbeef-a", phase="Succeeded"),
        ]
    )
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.return_value = _make_job_status(
        active=0,
        succeeded=1,
        failed=0,
    )

    client = _make_client(core_v1=core_v1, batch_v1=batch_v1)
    handler = _make_handler(client=client)
    request = _make_request(requested_count=2, job_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "partial"
    assert result.fulfilment.running_count == 1


def test_check_hosts_status_job_missing_falls_back_to_pod_rollup() -> None:
    """If the Job is gone but pods are still terminating, the handler
    should still produce a sensible roll-up from the pod list."""
    from kubernetes.client.exceptions import ApiException

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.side_effect = ApiException(status=404, reason="Not Found")

    client = _make_client(core_v1=core_v1, batch_v1=batch_v1)
    handler = _make_handler(client=client)
    handler._max_retries = 1
    request = _make_request(requested_count=2, job_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    # No pods + no Job + non-zero target => still in_progress so
    # callers retry rather than failing.
    assert result.fulfilment.state == "in_progress"
    assert result.instances == []


def test_check_hosts_status_handles_list_failure() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.side_effect = RuntimeError("apiserver down")
    batch_v1 = MagicMock()
    client = _make_client(core_v1=core_v1, batch_v1=batch_v1)
    handler = _make_handler(client=client)
    handler._max_retries = 1
    request = _make_request(requested_count=2, job_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "in_progress"
    assert "apiserver down" in result.fulfilment.message


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------


def test_get_example_templates_returns_job_example() -> None:
    examples = K8sJobHandler.get_example_templates()
    assert len(examples) >= 1
    example = examples[0]
    assert example.provider_api == "Job"
    assert example.provider_type == "k8s"
    # A Job needs a shell to run its run-to-completion command; the pause image
    # (used by the long-running kinds) has none, so the Job example uses busybox.
    assert example.image_id == "busybox:1.37"
    assert example.command == ["sh", "-c", "exit 0"]


# ---------------------------------------------------------------------------
# Provider-API key
# ---------------------------------------------------------------------------


def test_provider_api_key_matches_value_object() -> None:
    """The handler's PROVIDER_API key must match the enum value used
    by the strategy dispatch."""
    from orb.providers.k8s.value_objects import KubernetesProviderApi

    assert K8sJobHandler.PROVIDER_API == KubernetesProviderApi.JOB.value
