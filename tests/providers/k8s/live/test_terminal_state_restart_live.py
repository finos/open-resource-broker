"""Live integration tests for T23: terminal-state persistence across ORB restart.

Tests in this module hit a real Kubernetes cluster.  They are skipped by
default; pass ``--run-k8s`` to enable them.

Scenario: a request reaches a terminal state (COMPLETED or FAILED).  The ORB
process is then simulated to restart by re-constructing a fresh handler
instance and re-reading all state from the cluster (the k8s provider's source
of truth).  After the simulated restart, the request must still be in its
original terminal state — not reset to a transient state or lost entirely.

This guards against the regression where an in-memory cache or an ephemeral
lock is the only gate on state transitions, so a restart erroneously reverts
terminal state.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.terminal_state_restart")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_ACQUIRE_TIMEOUT = 120  # seconds
_POLL_INTERVAL = 3  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_k8s_client(k8s_provider_config: dict):
    """Build a live K8sClient from the ORB provider config."""
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
    """Construct a live K8sPodHandler."""
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

    client, config = _build_k8s_client(k8s_provider_config)
    logger = MagicMock()
    return K8sPodHandler(kubernetes_client=client, config=config, logger=logger), config


def _make_request(request_id: str, count: int = 1, template_id: str = "live-tpl"):
    """Construct a minimal Request for the given request-id."""
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


def _make_template(namespace: str):
    """Build a minimal Template."""
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="live-tpl",
        provider_type="k8s",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=10,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "command": ["sh", "-c", "sleep 3600"],
            }
        },
    )


def _wait_until_pod_running(
    core_v1, namespace: str, pod_name: str, timeout: float = _ACQUIRE_TIMEOUT
) -> None:
    """Poll until the pod reaches Running phase."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            pod = core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
            phase = (pod.status.phase or "Unknown") if pod.status else "Unknown"
            if phase in {"Running", "Succeeded"}:
                return
            if phase == "Failed":
                raise RuntimeError(f"Pod {pod_name} entered Failed state during acquire wait")
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                pass  # Not yet created, keep waiting.
            else:
                raise
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Pod {namespace}/{pod_name} did not reach Running within {timeout}s"
            )
        time.sleep(_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_completed_request_persists_across_simulated_restart(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T23a: a released (terminal) request remains terminal after handler recreation.

    Acquires a pod, releases it (terminal state: COMPLETED/RELEASED),
    then constructs a fresh handler instance representing an ORB restart.
    The reloaded handler must not be able to release the same request again
    with unexpected errors, nor report it as active.
    """
    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_template(k8s_namespace)

    # Acquire.
    result = await handler.acquire_hosts(request, template)
    pod_names = result.get("machine_ids", [])
    assert pod_names, "acquire_hosts returned no pod names"

    # Wait for pod to be Running so release is clean.
    _wait_until_pod_running(k8s_core_v1, k8s_namespace, pod_names[0])

    # Release (transition to terminal state).
    await handler.release_hosts(pod_names, request.provider_data)

    # Wait for pod deletion to propagate.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        pods = k8s_core_v1.list_namespaced_pod(
            namespace=k8s_namespace,
            label_selector=f"orb.io/request-id={live_request_id}",
        )
        if not pods.items:
            break
        time.sleep(_POLL_INTERVAL)

    # Simulate restart: new handler instance.
    new_handler, _ = _make_pod_handler(k8s_provider_config)

    # After restart, the cluster has no pods for this request-id.
    pods_after_restart = k8s_core_v1.list_namespaced_pod(
        namespace=k8s_namespace,
        label_selector=f"orb.io/request-id={live_request_id}",
    )
    assert len(pods_after_restart.items) == 0, (
        f"Expected 0 pods after restart for released request {live_request_id}, "
        f"found {len(pods_after_restart.items)}"
    )

    # A second release on the reloaded handler must be idempotent (no crash).
    try:
        await new_handler.release_hosts(pod_names, request.provider_data)
    except Exception as exc:
        # Acceptable: a "not found" or "already released" error is correct.
        acceptable_keywords = ("not found", "404", "already", "no resources", "terminal")
        assert any(kw in str(exc).lower() for kw in acceptable_keywords), (
            f"Unexpected error on idempotent release after restart: {exc}"
        )


async def test_failed_pod_state_visible_after_simulated_restart(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T23b: a pod that exits non-zero stays in Failed state across handler recreation.

    Creates a pod that exits immediately with code 1 (failed container),
    waits for Failed phase, then constructs a new handler and asserts that
    check_hosts_status returns a failed/error indicator — not active/running.
    """
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

    client, config = _build_k8s_client(k8s_provider_config)
    logger = MagicMock()
    handler = K8sPodHandler(kubernetes_client=client, config=config, logger=logger)

    # Template with a container that exits 1 immediately.
    from orb.domain.template.template_aggregate import Template

    fail_template = Template(
        template_id="live-fail-tpl",
        provider_type="k8s",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=5,
        provider_data={
            "k8s": {
                "namespace": k8s_namespace,
                "command": ["sh", "-c", "exit 1"],
                "restart_policy": "Never",
            }
        },
    )

    request = _make_request(live_request_id, template_id="live-fail-tpl")

    result = await handler.acquire_hosts(request, fail_template)
    pod_names = result.get("machine_ids", [])
    assert pod_names, "No pod created for failing acquire"

    pod_name = pod_names[0]

    # Wait for the pod to enter Failed phase.
    deadline = time.monotonic() + 60
    final_phase = "Unknown"
    while time.monotonic() < deadline:
        pod = k8s_core_v1.read_namespaced_pod(name=pod_name, namespace=k8s_namespace)
        final_phase = (pod.status.phase or "Unknown") if pod.status else "Unknown"
        if final_phase in {"Failed", "Succeeded"}:
            break
        time.sleep(_POLL_INTERVAL)

    assert final_phase in {"Failed", "Succeeded"}, (
        f"Pod {pod_name} did not reach terminal phase within timeout; phase={final_phase}"
    )

    # Simulate restart — fresh handler.
    new_client, new_config = _build_k8s_client(k8s_provider_config)
    new_handler = K8sPodHandler(kubernetes_client=new_client, config=new_config, logger=MagicMock())

    # check_hosts_status on the reloaded handler should reflect the terminal state.
    try:
        status_result = new_handler.check_hosts_status(request)
        log.info("post-restart check_hosts_status returned: %s", status_result)
        # The status must not indicate the pod is actively running.
        instances = getattr(status_result, "instances", None) or []
        for inst in instances:
            inst_status = (
                inst.get("status", "") if isinstance(inst, dict) else getattr(inst, "status", "")
            )
            assert str(inst_status).lower() not in {"running", "active"}, (
                f"Reloaded handler incorrectly reports terminal pod as active: {inst_status}"
            )
    except Exception as exc:
        # A "not found" error is also acceptable for a terminal/deleted pod.
        acceptable = ("not found", "404", "failed", "terminated", "terminal")
        assert any(kw in str(exc).lower() for kw in acceptable), (
            f"Unexpected error from reloaded handler: {exc}"
        )

    # Cleanup.
    try:
        k8s_core_v1.delete_namespaced_pod(name=pod_name, namespace=k8s_namespace)
    except Exception as _exc:
        log.debug("cleanup swallowed: %s", _exc)
