"""kmock-backed tests for :class:`K8sJobHandler`.

Tests run the real kubernetes SDK against a kmock HTTP server.

Covered scenarios
-----------------

* acquire_hosts POSTs a Job with parallelism == completions == requested_count.
* release_hosts DELETEs the Job from the apiserver.
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
    job_name: str | None = None,
    namespace: str = "orb-test",
) -> Any:
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    # acquire_hosts writes parallelism into provider_data; release_hosts now
    # requires it to confirm the machine_ids cover the full Job before deleting.
    provider_data: dict[str, Any] = {"namespace": namespace, "parallelism": requested_count}
    if job_name:
        provider_data["job_name"] = job_name
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Job",
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data=provider_data,
    )


def _make_template(namespace: str = "orb-test") -> Any:
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="tpl-1",
        provider_type="k8s",
        provider_api="Job",
        image_id="busybox:latest",
        max_instances=10,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "container_image": "busybox:latest",
                "resource_requests": {"cpu": "100m", "memory": "64Mi"},
            }
        },
    )


def _make_job_handler(k8s_client_facade: Any, k8s_config: Any) -> Any:
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler

    return K8sJobHandler(
        kubernetes_client=k8s_client_facade,
        config=k8s_config,
        logger=MagicMock(),
    )


def _register_jobs_resource(kmock_k8s: KubernetesEmulator) -> Any:
    from kmock import resource

    job_res = resource("batch", "v1", "jobs")
    kmock_k8s.resources[job_res] = {
        "namespaced": True,
        "kind": "Job",
        "singular": "job",
        "verbs": ["get", "list", "create", "patch", "delete", "watch"],
        "shortnames": [],
        "categories": [],
        "subresources": ["status"],
    }
    return job_res


def _preload_job(
    kmock_k8s: KubernetesEmulator,
    *,
    name: str,
    namespace: str = "orb-test",
    parallelism: int = 2,
) -> None:
    from kmock import resource

    job_res = resource("batch", "v1", "jobs")
    kmock_k8s.objects[job_res, namespace, name] = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"orb.io/managed": "true"},
        },
        "spec": {
            "parallelism": parallelism,
            "completions": parallelism,
            "backoffLimit": 0,
        },
        "status": {},
    }


# ---------------------------------------------------------------------------
# acquire_hosts — POST /apis/batch/v1/namespaces/<ns>/jobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_handler_creates_job_with_parallelism_completions(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """acquire_hosts creates a Job with parallelism == completions == N.

    The ORB Job handler maps one request to one Job with both fields set
    to the requested count.  We verify the emulator stores the correct spec.
    """
    job_res = _register_jobs_resource(kmock_k8s)

    handler = _make_job_handler(k8s_client_facade, k8s_config)
    request = _make_request(requested_count=3)
    template = _make_template()

    result = await handler.acquire_hosts(request, template)

    assert len(result["resource_ids"]) == 1
    job_name = result["resource_ids"][0]
    assert job_name.startswith("orb-")

    stored = kmock_k8s.objects[job_res, "orb-test", job_name]
    assert stored is not None
    assert not stored.deleted
    spec = stored.raw.get("spec", {})
    assert spec.get("parallelism") == 3
    assert spec.get("completions") == 3


@pytest.mark.asyncio
async def test_job_handler_creates_job_with_backoff_limit_zero(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """acquire_hosts must always set spec.backoffLimit=0 (ORB owns retries)."""
    job_res = _register_jobs_resource(kmock_k8s)

    handler = _make_job_handler(k8s_client_facade, k8s_config)
    result = await handler.acquire_hosts(_make_request(requested_count=1), _make_template())
    job_name = result["resource_ids"][0]

    spec = kmock_k8s.objects[job_res, "orb-test", job_name].raw.get("spec", {})
    assert spec.get("backoffLimit") == 0


# ---------------------------------------------------------------------------
# release_hosts — DELETE /apis/batch/v1/namespaces/<ns>/jobs/<name>
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_handler_release_deletes_job(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """release_hosts issues a DELETE; the Job is gone from the emulator store."""
    job_res = _register_jobs_resource(kmock_k8s)

    job_name = f"orb-{str(uuid.uuid4())[:8]}"
    _preload_job(kmock_k8s, name=job_name, parallelism=1)

    handler = _make_job_handler(k8s_client_facade, k8s_config)
    handler._max_retries = 1

    # Single-pod Job (parallelism=1): releasing the one machine_id is a full
    # release, which the handler honours by deleting the whole Job.
    request = _make_request(requested_count=1, job_name=job_name)
    await handler.release_hosts([job_name], request.provider_data)

    stored = kmock_k8s.objects[job_res, "orb-test", job_name]
    assert stored.deleted


@pytest.mark.asyncio
async def test_job_handler_release_tolerates_already_deleted_job(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """release_hosts must not raise when the Job does not exist (404).

    The Job handler deletes the entire Job regardless of machine_ids.
    If the Job is already gone (e.g. completed and cleaned up), the 404
    must be swallowed.
    """
    _register_jobs_resource(kmock_k8s)

    handler = _make_job_handler(k8s_client_facade, k8s_config)
    handler._max_retries = 1

    request = _make_request(requested_count=1, job_name="ghost-job")
    # Should not raise — best-effort 404 tolerance.
    await handler.release_hosts(["ghost-job"], request.provider_data)


# ---------------------------------------------------------------------------
# check_hosts_status — reads pods via label-selector + Job status
# ---------------------------------------------------------------------------


def _preload_job_pods(
    kmock_k8s: KubernetesEmulator,
    *,
    job_name: str,
    request_id: str,
    namespace: str = "orb-test",
    count: int = 2,
    phase: str = "Running",
    ready: bool = True,
) -> list[str]:
    """Seed kmock with pods belonging to a Job-managed request."""
    from kmock import resource

    pod_res = resource("", "v1", "pods")
    names = []
    for i in range(count):
        pod_name = f"{job_name}-pod-{i}"
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
                    "orb.io/provider-api": "Job",
                },
            },
            "spec": {
                "containers": [{"name": "app", "image": "busybox:latest"}],
            },
            "status": {
                "phase": phase,
                "podIP": "10.0.0.3" if phase == "Running" else None,
                "hostIP": "10.1.0.3" if phase == "Running" else None,
                "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
                "containerStatuses": [],
            },
        }
    return names


def _make_request_with_id(
    request_id: str,
    *,
    requested_count: int = 2,
    job_name: str | None = None,
    namespace: str = "orb-test",
) -> Any:
    """Build a real Request aggregate with the given string request_id."""
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    # acquire_hosts writes parallelism into provider_data; release_hosts now
    # requires it to confirm the machine_ids cover the full Job before deleting.
    provider_data: dict[str, Any] = {"namespace": namespace, "parallelism": requested_count}
    if job_name:
        provider_data["job_name"] = job_name
    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Job",
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data=provider_data,
    )


@pytest.mark.asyncio
async def test_job_handler_check_status_running_pods(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """check_hosts_status sees Running pods and reports running instances.

    The Job object is absent; the controller view is empty and the verdict
    comes from the pod roll-up only.
    """
    import asyncio

    from kmock import resource

    request_id = f"req-{uuid.uuid4()}"
    job_name = f"orb-{str(uuid.uuid4())[:8]}"

    job_res = resource("batch", "v1", "jobs")
    kmock_k8s.resources[job_res] = {
        "namespaced": True,
        "kind": "Job",
        "singular": "job",
        "verbs": ["get", "list", "create", "patch", "delete", "watch"],
        "shortnames": [],
        "categories": [],
        "subresources": ["status"],
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

    _preload_job_pods(
        kmock_k8s,
        job_name=job_name,
        request_id=request_id,
        count=2,
        phase="Running",
        ready=True,
    )

    handler = _make_job_handler(k8s_client_facade, k8s_config)
    request = _make_request_with_id(request_id, requested_count=2, job_name=job_name)

    result = await asyncio.to_thread(handler.check_hosts_status, request)

    assert len(result.instances) == 2
    for inst in result.instances:
        assert inst["status"] == "running"


@pytest.mark.asyncio
async def test_job_handler_check_status_no_pods_returns_in_progress(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """check_hosts_status with no pods returns in_progress (Job not yet running)."""
    import asyncio

    from kmock import resource

    request_id = f"req-{uuid.uuid4()}"
    job_name = f"orb-{str(uuid.uuid4())[:8]}"

    job_res = resource("batch", "v1", "jobs")
    kmock_k8s.resources[job_res] = {
        "namespaced": True,
        "kind": "Job",
        "singular": "job",
        "verbs": ["get", "list", "create", "patch", "delete", "watch"],
        "shortnames": [],
        "categories": [],
        "subresources": ["status"],
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

    handler = _make_job_handler(k8s_client_facade, k8s_config)
    request = _make_request_with_id(request_id, requested_count=2, job_name=job_name)

    result = await asyncio.to_thread(handler.check_hosts_status, request)

    assert result.instances == []
    assert result.fulfilment.state == "in_progress"
