"""Live integration tests for :class:`K8sPodHandler`.

Tests in this module hit a real Kubernetes cluster.  They are skipped by
default; pass ``--run-k8s`` to enable them.

Each test acquires pods via the handler's ``acquire_hosts`` method, then
exercises the status and/or release paths on real cluster state.  All
pods are labelled ``orb.io/managed=true`` and ``orb.io/request-id=<id>``
so the session-scoped nuclear-cleanup fixture in ``conftest.py`` removes
any strays that survive a failed test.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.pod")

pytestmark = [pytest.mark.asyncio]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_POD_READY_TIMEOUT = 120  # seconds — generous for slow clusters
_POD_DELETE_TIMEOUT = 60  # seconds
_POLL_INTERVAL = 3  # seconds between status polls


def _build_k8s_client(k8s_provider_config: dict):
    """Build a live :class:`K8sClient` from the ORB provider config."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
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
    return client, config


def _make_pod_handler(k8s_provider_config: dict):
    """Construct a live :class:`K8sPodHandler`."""
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

    client, config = _build_k8s_client(k8s_provider_config)
    logger = MagicMock()
    return K8sPodHandler(kubernetes_client=client, config=config, logger=logger), config


def _make_request(request_id: str, count: int = 1, template_id: str = "live-tpl"):
    """Construct a minimal :class:`Request` for the given request-id."""
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id=template_id,
        requested_count=count,
        provider_data={},
    )


def _make_template(namespace: str, image: str = "busybox:latest", command: list | None = None):
    """Construct a minimal :class:`K8sTemplate` for pod tests.

    Kubernetes-specific fields (namespace, command) are set as typed
    ``K8sTemplate`` attributes so they reach the pod spec — the generic
    ``Template`` aggregate has no ``provider_data`` surface, so those
    fields would be silently dropped if set there.
    """
    from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate

    return K8sTemplate(
        template_id="live-tpl",
        provider_api="Pod",
        machine_image=image,
        max_machines=10,
        namespace=namespace,
        command=command or ["sh", "-c", "sleep 3600"],
    )


def _wait_for_pod_phase(
    core_v1,
    namespace: str,
    pod_name: str,
    target_phases: set[str],
    timeout: float = _POD_READY_TIMEOUT,
) -> str:
    """Poll until the pod reaches one of ``target_phases``.

    Returns the final phase string, or raises ``TimeoutError``.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            pod = core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
            phase = (pod.status.phase or "Unknown") if pod.status else "Unknown"
            if phase in target_phases:
                return phase
        except Exception as exc:
            # 404 is acceptable when waiting for deletion.
            if getattr(exc, "status", None) == 404 and "Terminated" in target_phases:
                return "Terminated"
            log.debug("read_namespaced_pod raised for %s: %s", pod_name, exc)
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Pod {namespace}/{pod_name} did not reach {target_phases} within {timeout}s"
            )
        time.sleep(_POLL_INTERVAL)


def _pod_exists(core_v1, namespace: str, pod_name: str) -> bool:
    """Return True when the pod exists in the cluster."""
    try:
        core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        return True
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            return False
        raise


def _wait_until_pod_gone(
    core_v1, namespace: str, pod_name: str, timeout: float = _POD_DELETE_TIMEOUT
) -> None:
    """Poll until the pod disappears from the cluster."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pod_exists(core_v1, namespace, pod_name):
            return
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"Pod {namespace}/{pod_name} not deleted within {timeout}s")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_pod_acquire_reaches_running_status(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Acquire one pod, poll status, verify it reaches ``running`` within the timeout."""
    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_template(k8s_namespace, command=["sh", "-c", "sleep 3600"])

    result = await handler.acquire_hosts(request, template)
    pod_names = result.get("machine_ids", [])
    assert len(pod_names) == 1, f"Expected 1 pod, got {pod_names!r}"

    pod_name = pod_names[0]
    log.info("Acquired pod %s/%s", k8s_namespace, pod_name)

    phase = _wait_for_pod_phase(
        k8s_core_v1,
        k8s_namespace,
        pod_name,
        target_phases={"Running"},
        timeout=_POD_READY_TIMEOUT,
    )
    assert phase == "Running", f"Expected Running, got {phase}"

    # Also exercise the ORB status path.
    request_for_status = _make_request(live_request_id, count=1, template_id="live-tpl")
    request_for_status.provider_data = {"namespace": k8s_namespace}  # type: ignore[assignment]
    status_result = handler.check_hosts_status(request_for_status)
    statuses = [inst.get("status") for inst in (status_result.instances or [])]
    assert "running" in statuses, f"Expected at least one pod in 'running' status, got: {statuses}"

    # Cleanup
    await handler.release_hosts(pod_names, request.provider_data)


async def test_pod_release_removes_from_cluster(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Acquire a pod, release it, verify the pod is deleted from the cluster."""
    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_template(k8s_namespace, command=["sh", "-c", "sleep 3600"])

    result = await handler.acquire_hosts(request, template)
    pod_names = result.get("machine_ids", [])
    assert len(pod_names) == 1

    pod_name = pod_names[0]
    assert _pod_exists(k8s_core_v1, k8s_namespace, pod_name), (
        f"Pod {pod_name} should exist immediately after acquire"
    )

    await handler.release_hosts(pod_names, request.provider_data)

    _wait_until_pod_gone(k8s_core_v1, k8s_namespace, pod_name, timeout=_POD_DELETE_TIMEOUT)
    assert not _pod_exists(k8s_core_v1, k8s_namespace, pod_name), (
        f"Pod {pod_name} still present after release"
    )


async def test_pod_status_after_release_is_terminated(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """After release, status query should not find running pods for the request."""
    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_template(k8s_namespace, command=["sh", "-c", "sleep 3600"])

    result = await handler.acquire_hosts(request, template)
    pod_names = result.get("machine_ids", [])
    assert len(pod_names) == 1

    pod_name = pod_names[0]
    # Release the pod.
    await handler.release_hosts(pod_names, request.provider_data)
    _wait_until_pod_gone(k8s_core_v1, k8s_namespace, pod_name, timeout=_POD_DELETE_TIMEOUT)

    # Status query with on-demand list (no watcher) should return no running pods.
    status_result = handler.check_hosts_status(request)
    running = [inst for inst in (status_result.instances or []) if inst.get("status") == "running"]
    assert not running, f"Expected 0 running pods after release, got: {running}"


async def test_pod_acquire_bad_image_reaches_failed(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """Acquire a pod with an unpullable image; verify it moves to Failed or ErrImagePull.

    The pod should reach phase Failed (image pull error) or remain Pending
    with an ImagePullBackOff/ErrImagePull waiting reason.  Either outcome
    confirms the handler submitted the pod correctly and the cluster
    diagnosed the pull failure.
    """
    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    # Use a definitively non-existent image tag to guarantee a pull failure.
    bad_image = "k8s.gcr.io/definitely-does-not-exist-orb-live-test:nope"
    template = _make_template(k8s_namespace, image=bad_image, command=["echo", "hi"])

    result = await handler.acquire_hosts(request, template)
    pod_names = result.get("machine_ids", [])
    assert len(pod_names) == 1, f"Expected 1 pod submitted, got {pod_names!r}"

    pod_name = pod_names[0]
    log.info("Acquired pod with bad image: %s/%s", k8s_namespace, pod_name)

    # Wait for the pod to hit a terminal-ish state (Failed) or stay Pending
    # with a pull error.  We accept either outcome since cluster behaviour
    # varies (some clusters have pull retries that keep it Pending).
    deadline = time.monotonic() + 90
    final_phase = None
    final_reason = None
    while time.monotonic() < deadline:
        try:
            pod = k8s_core_v1.read_namespaced_pod(name=pod_name, namespace=k8s_namespace)
            phase = (pod.status.phase or "") if pod.status else ""
            container_statuses = list(pod.status.container_statuses or []) if pod.status else []
            for cs in container_statuses:
                waiting = getattr(cs.state, "waiting", None) if cs.state else None
                if waiting and waiting.reason in (
                    "ErrImagePull",
                    "ImagePullBackOff",
                    "InvalidImageName",
                ):
                    final_phase = phase
                    final_reason = waiting.reason
                    break
            if final_reason or phase == "Failed":
                final_phase = phase or final_phase
                break
        except Exception as exc:
            log.debug("read pod failed: %s", exc)
        time.sleep(_POLL_INTERVAL)

    assert final_phase is not None or final_reason is not None, (
        f"Pod {pod_name} did not reach a pull-error state within 90s "
        f"(phase={final_phase!r}, reason={final_reason!r})"
    )
    log.info(
        "Pod %s reached expected pull-error state: phase=%s reason=%s",
        pod_name,
        final_phase,
        final_reason,
    )

    # Cleanup
    await handler.release_hosts(pod_names, request.provider_data)
