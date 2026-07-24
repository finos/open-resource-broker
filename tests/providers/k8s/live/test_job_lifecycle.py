"""Live integration tests for :class:`K8sJobHandler`.

Tests in this module hit a real Kubernetes cluster.  Pass ``--run-k8s``
to enable them.

Job semantics: one Job with ``parallelism = completions = N``.  Release
is always a whole-Job delete (selective release is not supported).
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.job")

pytestmark = [pytest.mark.asyncio]

_JOB_TIMEOUT = 180  # seconds to wait for job completion
_DELETE_TIMEOUT = 60  # seconds
_POLL_INTERVAL = 5  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_job_handler(k8s_provider_config: dict):
    """Construct a live :class:`K8sJobHandler`."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler
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
    return K8sJobHandler(kubernetes_client=client, config=config, logger=logger), config


def _make_request(request_id: str, count: int = 2):
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Job",
        template_id="live-job-tpl",
        requested_count=count,
        provider_data={},
    )


def _make_template(namespace: str, image: str = "busybox:latest", command: list | None = None):
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="live-job-tpl",
        provider_type="k8s",
        provider_api="Job",
        image_id=image,
        max_instances=10,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "command": command or ["sh", "-c", "sleep 3600"],
            }
        },
    )


def _read_job(batch_v1, namespace: str, job_name: str):
    try:
        return batch_v1.read_namespaced_job(name=job_name, namespace=namespace)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            return None
        raise


def _job_exists(batch_v1, namespace: str, job_name: str) -> bool:
    return _read_job(batch_v1, namespace, job_name) is not None


def _wait_until_job_gone(
    batch_v1, namespace: str, job_name: str, timeout: float = _DELETE_TIMEOUT
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _job_exists(batch_v1, namespace, job_name):
            return
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"Job {namespace}/{job_name} not deleted within {timeout}s")


def _wait_for_job_complete(
    batch_v1, namespace: str, job_name: str, timeout: float = _JOB_TIMEOUT
) -> bool:
    """Poll until Job has ``Complete`` condition; return True on completion."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = _read_job(batch_v1, namespace, job_name)
        if job is None:
            return False
        conditions = list(job.status.conditions or []) if job.status else []
        for cond in conditions:
            if cond.type == "Complete" and cond.status == "True":
                return True
        time.sleep(_POLL_INTERVAL)
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_job_acquire_creates_parallelism_completions(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_batch_v1,
    live_request_id: str,
) -> None:
    """Acquire 2 units; verify Job has ``parallelism=2`` and ``completions=2``."""
    handler, _ = _build_job_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=2)
    template = _make_template(k8s_namespace, command=["sh", "-c", "sleep 3600"])

    result = await handler.acquire_hosts(request, template)
    job_names = result.get("resource_ids", [])
    assert len(job_names) == 1, f"Expected 1 job name, got {job_names!r}"
    job_name = job_names[0]
    log.info("Acquired Job %s/%s", k8s_namespace, job_name)
    request.provider_data = {  # type: ignore[assignment]
        "namespace": k8s_namespace,
        "job_name": job_name,
    }

    job = k8s_batch_v1.read_namespaced_job(name=job_name, namespace=k8s_namespace)
    assert job is not None, f"Job {job_name} not found in cluster"

    parallelism = job.spec.parallelism if job.spec else None
    completions = job.spec.completions if job.spec else None
    assert parallelism == 2, f"Expected parallelism=2, got {parallelism}"
    assert completions == 2, f"Expected completions=2, got {completions}"

    # Verify ORB labels are present on the Job pod template.
    pod_labels = (
        job.spec.template.metadata.labels
        if job.spec and job.spec.template and job.spec.template.metadata
        else {}
    )
    assert pod_labels.get("orb.io/managed") == "true", (
        f"Job pod template missing orb.io/managed label: {pod_labels!r}"
    )
    assert pod_labels.get("orb.io/request-id") == live_request_id, (
        f"Job pod template missing orb.io/request-id label: {pod_labels!r}"
    )

    # Cleanup: release the whole Job.  The Job controller does not support
    # subset release, so the handler requires the machine_ids to cover every
    # pod of the Job (``len(machine_ids) >= parallelism``).  This is a
    # whole-Job delete; the ids themselves are informational.
    full_release_ids = [f"{job_name}-{i}" for i in range(parallelism)]
    await handler.release_hosts(full_release_ids, request.provider_data)
    _wait_until_job_gone(k8s_batch_v1, k8s_namespace, job_name)


async def test_job_completion_status_is_completed(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_batch_v1,
    live_request_id: str,
) -> None:
    """Acquire a short Job (``echo done``); wait for completion; verify Complete condition.

    ``backoffLimit=0`` is the ORB invariant so the job must not restart on
    failure.  ``echo done`` exits 0, so the Job should reach Complete.
    """
    handler, _ = _build_job_handler(k8s_provider_config)
    # Count=1 is simplest for a completion test.
    request = _make_request(live_request_id, count=1)
    template = _make_template(k8s_namespace, command=["sh", "-c", "echo done"])

    result = await handler.acquire_hosts(request, template)
    job_name = result["resource_ids"][0]
    request.provider_data = {  # type: ignore[assignment]
        "namespace": k8s_namespace,
        "job_name": job_name,
    }
    log.info("Acquired short Job %s/%s", k8s_namespace, job_name)

    completed = _wait_for_job_complete(k8s_batch_v1, k8s_namespace, job_name, timeout=_JOB_TIMEOUT)
    assert completed, f"Job {job_name} did not reach Complete condition within {_JOB_TIMEOUT}s"
    log.info("Job %s/%s completed successfully", k8s_namespace, job_name)

    # Cleanup
    await handler.release_hosts(["placeholder"], request.provider_data)
    _wait_until_job_gone(k8s_batch_v1, k8s_namespace, job_name)


async def test_job_release_deletes_job_and_pods(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    k8s_batch_v1,
    live_request_id: str,
) -> None:
    """Acquire a Job; release it; verify the Job and its pods are deleted."""
    handler, _ = _build_job_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_template(k8s_namespace, command=["sh", "-c", "sleep 3600"])

    result = await handler.acquire_hosts(request, template)
    job_name = result["resource_ids"][0]
    request.provider_data = {  # type: ignore[assignment]
        "namespace": k8s_namespace,
        "job_name": job_name,
    }

    assert _job_exists(k8s_batch_v1, k8s_namespace, job_name), (
        f"Job {job_name} should exist immediately after acquire"
    )

    # Release the whole Job.
    await handler.release_hosts(["placeholder"], request.provider_data)

    # The Job itself should disappear.
    _wait_until_job_gone(k8s_batch_v1, k8s_namespace, job_name, timeout=_DELETE_TIMEOUT)
    assert not _job_exists(k8s_batch_v1, k8s_namespace, job_name), (
        f"Job {job_name} still present after release"
    )

    # Pods owned by the deleted Job should also be gone (Background propagation).
    label_selector = f"orb.io/request-id={live_request_id}"
    deadline = time.monotonic() + _DELETE_TIMEOUT
    while time.monotonic() < deadline:
        pods = (
            k8s_core_v1.list_namespaced_pod(
                namespace=k8s_namespace, label_selector=label_selector
            ).items
            or []
        )
        running_pods = [
            p
            for p in pods
            if (p.status.phase or "") not in ("Succeeded", "Failed")
            and p.metadata.deletion_timestamp is None
        ]
        if not running_pods:
            break
        time.sleep(_POLL_INTERVAL)
    else:
        still_there = [p.metadata.name for p in running_pods]  # type: ignore[possibly-undefined]
        pytest.fail(f"Pods still present after Job {job_name} deleted: {still_there!r}")
