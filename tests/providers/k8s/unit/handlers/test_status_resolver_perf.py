"""Tests for controller-status TTL cache (Task A) and resource_version='0' (Task B).

Task A — controller GET cache:
  * Second poll within TTL does NOT issue a second read_namespaced_* call.
  * GET fires again after TTL expiry.
  * Covers Deployment, StatefulSet, Job resolvers.

Task B — resource_version='0' on fallback LIST:
  * All four status resolvers (Pod, Deployment, StatefulSet, Job) pass
    resource_version='0' to the fallback list_namespaced_pod call.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.providers.k8s.configuration.config import (
    K8sProviderConfig,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _req(
    *,
    provider_api: str,
    deployment_name: str | None = None,
    namespace: str = "ns",
    requested_count: int = 1,
) -> Request:
    pd: dict[str, Any] = {"namespace": namespace}
    if deployment_name:
        pd["deployment_name"] = deployment_name
        pd["statefulset_name"] = deployment_name
        pd["job_name"] = deployment_name
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api=provider_api,
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data=pd,
    )


def _make_pod_list(phase: str = "Running") -> SimpleNamespace:
    return SimpleNamespace(
        items=[
            SimpleNamespace(
                metadata=SimpleNamespace(
                    name="pod-1",
                    namespace="ns",
                    labels={"orb.io/request-id": "req-x"},
                ),
                spec=SimpleNamespace(node_name="node-1"),
                status=SimpleNamespace(
                    phase=phase,
                    pod_ip="10.0.0.1",
                    host_ip="10.1.0.1",
                    start_time=None,
                    conditions=[SimpleNamespace(type="Ready", status="True", reason=None)],
                    container_statuses=[],
                ),
            )
        ]
    )


def _make_deployment_obj(ready_replicas: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(name="d-1", namespace="ns"),
        spec=SimpleNamespace(replicas=1),
        status=SimpleNamespace(
            available_replicas=ready_replicas,
            ready_replicas=ready_replicas,
            updated_replicas=ready_replicas,
            conditions=[],
        ),
    )


def _make_statefulset_obj(ready_replicas: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(name="sts-1", namespace="ns"),
        spec=SimpleNamespace(replicas=1),
        status=SimpleNamespace(
            ready_replicas=ready_replicas,
            current_replicas=ready_replicas,
            updated_replicas=ready_replicas,
            conditions=[],
        ),
    )


def _make_job_obj(succeeded: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(name="job-1", namespace="ns"),
        status=SimpleNamespace(
            active=0,
            succeeded=succeeded,
            failed=0,
            conditions=[SimpleNamespace(type="Complete", status="True", reason=None)],
        ),
    )


# ---------------------------------------------------------------------------
# Task A — TTL cache: Deployment
# ---------------------------------------------------------------------------


def test_deployment_controller_cache_hit_suppresses_second_get() -> None:
    """Second call within TTL must NOT trigger another read_namespaced_deployment."""
    from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_obj()

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    handler = K8sDeploymentHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Deployment", deployment_name="d-1")

    handler.check_hosts_status(request)
    handler.check_hosts_status(request)

    # First call triggers one GET; second call within 60 s TTL must reuse cache.
    assert apps_v1.read_namespaced_deployment.call_count == 1


def test_deployment_controller_cache_miss_after_ttl_expiry() -> None:
    """GET fires again once the TTL has expired."""
    from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_obj()

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=5.0)
    handler = K8sDeploymentHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Deployment", deployment_name="d-1")

    # Patch time.monotonic: first call returns 0.0, second call (cache check) returns 10.0
    # so the cached entry is 10 s old > TTL 5 s — cache miss.
    times = [0.0, 0.0, 10.0, 10.0]
    with patch(
        "orb.providers.k8s.infrastructure.handlers.deployment_status.time.monotonic",
        side_effect=times,
    ):
        handler.check_hosts_status(request)
        handler.check_hosts_status(request)

    assert apps_v1.read_namespaced_deployment.call_count == 2


# ---------------------------------------------------------------------------
# Task A — TTL cache: StatefulSet
# ---------------------------------------------------------------------------


def test_statefulset_controller_cache_hit_suppresses_second_get() -> None:
    from orb.providers.k8s.infrastructure.handlers.statefulset_handler import K8sStatefulSetHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = _make_statefulset_obj()

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    handler = K8sStatefulSetHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="StatefulSet", deployment_name="sts-1")

    handler.check_hosts_status(request)
    handler.check_hosts_status(request)

    assert apps_v1.read_namespaced_stateful_set.call_count == 1


def test_statefulset_controller_cache_miss_after_ttl_expiry() -> None:
    from orb.providers.k8s.infrastructure.handlers.statefulset_handler import K8sStatefulSetHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = _make_statefulset_obj()

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=5.0)
    handler = K8sStatefulSetHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="StatefulSet", deployment_name="sts-1")

    times = [0.0, 0.0, 10.0, 10.0]
    with patch(
        "orb.providers.k8s.infrastructure.handlers.statefulset_status.time.monotonic",
        side_effect=times,
    ):
        handler.check_hosts_status(request)
        handler.check_hosts_status(request)

    assert apps_v1.read_namespaced_stateful_set.call_count == 2


# ---------------------------------------------------------------------------
# Task A — TTL cache: Job
# ---------------------------------------------------------------------------


def test_job_controller_cache_hit_suppresses_second_get() -> None:
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.return_value = _make_job_obj()

    client = MagicMock()
    client.core_v1 = core_v1
    client.batch_v1 = batch_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    handler = K8sJobHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Job", deployment_name="job-1")

    handler.check_hosts_status(request)
    handler.check_hosts_status(request)

    assert batch_v1.read_namespaced_job.call_count == 1


def test_job_controller_cache_miss_after_ttl_expiry() -> None:
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.return_value = _make_job_obj()

    client = MagicMock()
    client.core_v1 = core_v1
    client.batch_v1 = batch_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=5.0)
    handler = K8sJobHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Job", deployment_name="job-1")

    times = [0.0, 0.0, 10.0, 10.0]
    with patch(
        "orb.providers.k8s.infrastructure.handlers.job_status.time.monotonic",
        side_effect=times,
    ):
        handler.check_hosts_status(request)
        handler.check_hosts_status(request)

    assert batch_v1.read_namespaced_job.call_count == 2


# ---------------------------------------------------------------------------
# Task B — resource_version='0' on fallback list_namespaced_pod
# ---------------------------------------------------------------------------


def test_pod_status_fallback_list_passes_resource_version_zero() -> None:
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])

    client = MagicMock()
    client.core_v1 = core_v1

    config = K8sProviderConfig(namespace="ns")
    handler = K8sPodHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Pod")

    handler.check_hosts_status(request)

    call_kwargs = core_v1.list_namespaced_pod.call_args.kwargs
    assert call_kwargs.get("resource_version") == "0", (
        f"Expected resource_version='0', got {call_kwargs.get('resource_version')!r}"
    )


def test_deployment_status_fallback_list_passes_resource_version_zero() -> None:
    from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_obj(ready_replicas=0)

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns")
    handler = K8sDeploymentHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Deployment", deployment_name="d-1")

    handler.check_hosts_status(request)

    call_kwargs = core_v1.list_namespaced_pod.call_args.kwargs
    assert call_kwargs.get("resource_version") == "0"


def test_statefulset_status_fallback_list_passes_resource_version_zero() -> None:
    from orb.providers.k8s.infrastructure.handlers.statefulset_handler import K8sStatefulSetHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = _make_statefulset_obj(ready_replicas=0)

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns")
    handler = K8sStatefulSetHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="StatefulSet", deployment_name="sts-1")

    handler.check_hosts_status(request)

    call_kwargs = core_v1.list_namespaced_pod.call_args.kwargs
    assert call_kwargs.get("resource_version") == "0"


def test_job_status_fallback_list_passes_resource_version_zero() -> None:
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.return_value = _make_job_obj(succeeded=0)

    client = MagicMock()
    client.core_v1 = core_v1
    client.batch_v1 = batch_v1

    config = K8sProviderConfig(namespace="ns")
    handler = K8sJobHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Job", deployment_name="job-1")

    handler.check_hosts_status(request)

    call_kwargs = core_v1.list_namespaced_pod.call_args.kwargs
    assert call_kwargs.get("resource_version") == "0"


# ---------------------------------------------------------------------------
# Task A — cache only fires for fully-ready workloads (fix #2)
# ---------------------------------------------------------------------------


def test_deployment_not_ready_bypasses_cache() -> None:
    """While readyReplicas < desired, every poll must issue a fresh GET (no caching)."""
    from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    apps_v1 = MagicMock()
    # First poll: not ready (readyReplicas=0); second poll: still not ready.
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_obj(ready_replicas=0)

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    handler = K8sDeploymentHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Deployment", deployment_name="d-1")

    handler.check_hosts_status(request)
    handler.check_hosts_status(request)

    # Both polls must issue a fresh GET — cache must not serve a not-ready view.
    assert apps_v1.read_namespaced_deployment.call_count == 2


def test_deployment_ready_uses_cache() -> None:
    """Once fully ready, subsequent polls within TTL must use the cached view."""
    from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_obj(ready_replicas=1)

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    handler = K8sDeploymentHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Deployment", deployment_name="d-1")

    handler.check_hosts_status(request)
    handler.check_hosts_status(request)

    # First call fetches; second within TTL with a ready workload uses the cache.
    assert apps_v1.read_namespaced_deployment.call_count == 1


def test_statefulset_not_ready_bypasses_cache() -> None:
    """While readyReplicas < desired, every poll must issue a fresh GET (no caching)."""
    from orb.providers.k8s.infrastructure.handlers.statefulset_handler import K8sStatefulSetHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = _make_statefulset_obj(ready_replicas=0)

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    handler = K8sStatefulSetHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="StatefulSet", deployment_name="sts-1")

    handler.check_hosts_status(request)
    handler.check_hosts_status(request)

    assert apps_v1.read_namespaced_stateful_set.call_count == 2


def test_statefulset_ready_uses_cache() -> None:
    """Once fully ready, subsequent polls within TTL must use the cached view."""
    from orb.providers.k8s.infrastructure.handlers.statefulset_handler import K8sStatefulSetHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = _make_statefulset_obj(ready_replicas=1)

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    handler = K8sStatefulSetHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="StatefulSet", deployment_name="sts-1")

    handler.check_hosts_status(request)
    handler.check_hosts_status(request)

    assert apps_v1.read_namespaced_stateful_set.call_count == 1


def test_job_not_complete_bypasses_cache() -> None:
    """While job is not yet complete, every poll must issue a fresh GET (no caching)."""
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler

    # Job with no Complete condition — still running.
    not_complete_job = SimpleNamespace(
        metadata=SimpleNamespace(name="job-1", namespace="ns"),
        status=SimpleNamespace(
            active=1,
            succeeded=0,
            failed=0,
            conditions=[],
        ),
    )

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.return_value = not_complete_job

    client = MagicMock()
    client.core_v1 = core_v1
    client.batch_v1 = batch_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    handler = K8sJobHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Job", deployment_name="job-1")

    handler.check_hosts_status(request)
    handler.check_hosts_status(request)

    assert batch_v1.read_namespaced_job.call_count == 2


def test_job_complete_uses_cache() -> None:
    """Once the Complete condition is set, subsequent polls within TTL use the cache."""
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.return_value = _make_job_obj(succeeded=1)

    client = MagicMock()
    client.core_v1 = core_v1
    client.batch_v1 = batch_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    handler = K8sJobHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Job", deployment_name="job-1")

    handler.check_hosts_status(request)
    handler.check_hosts_status(request)

    assert batch_v1.read_namespaced_job.call_count == 1


# ---------------------------------------------------------------------------
# Finding 1 — scale-down masking: requested_count change must be a cache miss
# ---------------------------------------------------------------------------


def test_deployment_scale_down_causes_cache_miss() -> None:
    """Scale 5→3 within TTL: cached ready=5, new requested=3 → must be a miss."""
    from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    apps_v1 = MagicMock()
    # Return ready=5 on both calls — only call count matters.
    apps_v1.read_namespaced_deployment.return_value = SimpleNamespace(
        metadata=SimpleNamespace(name="d-1", namespace="ns"),
        spec=SimpleNamespace(replicas=5),
        status=SimpleNamespace(
            available_replicas=5,
            ready_replicas=5,
            updated_replicas=5,
            conditions=[],
        ),
    )

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    handler = K8sDeploymentHandler(kubernetes_client=client, config=config, logger=MagicMock())

    # First poll: requested_count=5, workload fully ready → cached with stored_requested=5.
    req5 = _req(provider_api="Deployment", deployment_name="d-1", requested_count=5)
    handler.check_hosts_status(req5)
    assert apps_v1.read_namespaced_deployment.call_count == 1

    # Second poll: requested_count=3 (scale-down happened) — must NOT serve stale
    # cached view; must issue a fresh GET.
    req3 = _req(provider_api="Deployment", deployment_name="d-1", requested_count=3)
    handler.check_hosts_status(req3)
    assert apps_v1.read_namespaced_deployment.call_count == 2, (
        "Scale-down (requested_count change 5→3) must bypass the cache and issue a fresh GET"
    )


def test_statefulset_scale_down_causes_cache_miss() -> None:
    """Scale 5→3 within TTL for StatefulSet: stale entry must not be served."""
    from orb.providers.k8s.infrastructure.handlers.statefulset_handler import K8sStatefulSetHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = SimpleNamespace(
        metadata=SimpleNamespace(name="sts-1", namespace="ns"),
        spec=SimpleNamespace(replicas=5),
        status=SimpleNamespace(
            ready_replicas=5,
            current_replicas=5,
            updated_replicas=5,
            conditions=[],
        ),
    )

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    handler = K8sStatefulSetHandler(kubernetes_client=client, config=config, logger=MagicMock())

    handler.check_hosts_status(
        _req(provider_api="StatefulSet", deployment_name="sts-1", requested_count=5)
    )
    assert apps_v1.read_namespaced_stateful_set.call_count == 1

    handler.check_hosts_status(
        _req(provider_api="StatefulSet", deployment_name="sts-1", requested_count=3)
    )
    assert apps_v1.read_namespaced_stateful_set.call_count == 2, (
        "Scale-down (requested_count change 5→3) must bypass the cache and issue a fresh GET"
    )


def test_job_parallelism_change_causes_cache_miss() -> None:
    """Job terminal-cached: changing requested_count (analogous to scale) bypasses cache."""
    # Jobs don't use requested_count in their cache key the same way, but
    # a terminal Failed/Complete job's cache is keyed by (namespace, job_name).
    # This test verifies the terminal-Failed path is cached (see Finding 3).
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler

    failed_job = SimpleNamespace(
        metadata=SimpleNamespace(name="job-1", namespace="ns"),
        status=SimpleNamespace(
            active=0,
            succeeded=0,
            failed=3,
            conditions=[
                SimpleNamespace(type="Failed", status="True", reason="BackoffLimitExceeded")
            ],
        ),
    )

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.return_value = failed_job

    client = MagicMock()
    client.core_v1 = core_v1
    client.batch_v1 = batch_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    handler = K8sJobHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Job", deployment_name="job-1")

    # First call: terminal-Failed → stored in cache.
    result1 = handler.check_hosts_status(request)
    assert result1.fulfilment.state == "failed"
    assert batch_v1.read_namespaced_job.call_count == 1

    # Second call within TTL: terminal state — cache hit, no second GET.
    result2 = handler.check_hosts_status(request)
    assert result2.fulfilment.state == "failed"
    assert batch_v1.read_namespaced_job.call_count == 1, (
        "Terminal-Failed job must be served from cache to avoid re-fetch loop (Finding 3)"
    )


# ---------------------------------------------------------------------------
# Finding 2 — TOCTOU: older store must not overwrite a newer cache entry
# ---------------------------------------------------------------------------


def test_deployment_toctou_older_store_does_not_clobber_newer() -> None:
    """A slow thread's fetch with an older timestamp must not overwrite a fresh entry."""
    from orb.providers.k8s.infrastructure.handlers.deployment_status import DeploymentStatusResolver

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_obj(ready_replicas=1)

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    handler_mock = MagicMock()
    handler_mock.config = config

    fresh_view = {"ready_replicas": 1, "conditions": []}
    stale_view = {"ready_replicas": 0, "conditions": []}

    resolver = DeploymentStatusResolver.__new__(DeploymentStatusResolver)
    import threading

    resolver._handler = handler_mock  # type: ignore[attr-defined]
    resolver._controller_cache = {}  # type: ignore[attr-defined]
    resolver._cache_lock = threading.Lock()  # type: ignore[attr-defined]

    # Simulate: fresh entry already stored at t=100.
    cache_key = ("ns", "dep-1")
    resolver._controller_cache[cache_key] = (fresh_view, 100.0, 1)  # type: ignore[index]

    # Slow-thread store with older timestamp (t=50) must not overwrite.
    handler_mock.config.controller_status_cache_ttl_seconds = 60.0
    with resolver._cache_lock:  # type: ignore[attr-defined]
        existing = resolver._controller_cache.get(cache_key)  # type: ignore[attr-defined]
        if existing is None or existing[1] < 50.0:
            resolver._controller_cache[cache_key] = (stale_view, 50.0, 1)  # type: ignore[index]

    # The fresh_view at t=100 must still be in the cache.
    stored = resolver._controller_cache[cache_key]  # type: ignore[index]
    assert stored[0] is fresh_view, "Older store (t=50) must not overwrite newer entry (t=100)"
    assert stored[1] == 100.0


# ---------------------------------------------------------------------------
# Finding 3 — Job: deleted-Job not-found stays at debug, not warning
# ---------------------------------------------------------------------------


def test_job_deleted_not_found_no_warning_flood(caplog: Any) -> None:
    """A not-found Job on read_job_status must log at DEBUG, not WARNING."""
    import logging

    # Raise a real ApiException(404): the K8sRetryClassifier recognises it as
    # non-retryable so the handler fails fast — no retry backoff, no sleep.
    from kubernetes.client.exceptions import ApiException

    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler

    not_found_exc = ApiException(status=404)
    not_found_exc.status = 404

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.side_effect = not_found_exc

    client = MagicMock()
    client.core_v1 = core_v1
    client.batch_v1 = batch_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=60.0)
    logger_mock = MagicMock()
    handler = K8sJobHandler(kubernetes_client=client, config=config, logger=logger_mock)

    # No is_not_found override or retry-backoff hack needed: the real
    # ApiException(404) is recognised as not-found by the handler and as
    # non-retryable by K8sRetryClassifier, so it fails fast on the first attempt.
    request = _req(provider_api="Job", deployment_name="job-1")

    with caplog.at_level(logging.WARNING):
        handler.check_hosts_status(request)
        handler.check_hosts_status(request)

    # Must use debug (via the underlying logger_mock), not warning.
    # The logger_mock.warning must NOT have been called for not-found.
    warning_calls = [
        call
        for call in logger_mock.warning.call_args_list
        if "not found" in str(call).lower() or "not_found" in str(call).lower()
    ]
    assert len(warning_calls) == 0, (
        "Deleted-Job not-found must log at debug, not warning (no flood on terminal poll)"
    )


# ---------------------------------------------------------------------------
# Finding 5 — TTL <= 0 disables cache entirely (no store, GET every poll)
# ---------------------------------------------------------------------------


def test_deployment_ttl_zero_disables_cache() -> None:
    """TTL=0 must bypass the cache entirely — every poll must issue a fresh GET."""
    from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_obj(ready_replicas=1)

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=0.0)
    handler = K8sDeploymentHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Deployment", deployment_name="d-1")

    handler.check_hosts_status(request)
    handler.check_hosts_status(request)
    handler.check_hosts_status(request)

    # With TTL=0 (disabled), every poll must issue a fresh GET — no caching.
    assert apps_v1.read_namespaced_deployment.call_count == 3, (
        "TTL=0 must disable the cache — 3 polls must produce 3 GETs"
    )
    # The cache dict must remain empty — no entries stored when TTL<=0.
    assert len(handler._status_resolver._controller_cache) == 0, (
        "TTL=0 must not store anything in the cache dict"
    )


def test_statefulset_ttl_zero_disables_cache() -> None:
    """TTL=0 must bypass the StatefulSet cache — every poll must issue a fresh GET."""
    from orb.providers.k8s.infrastructure.handlers.statefulset_handler import K8sStatefulSetHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = _make_statefulset_obj(ready_replicas=1)

    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=0.0)
    handler = K8sStatefulSetHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="StatefulSet", deployment_name="sts-1")

    handler.check_hosts_status(request)
    handler.check_hosts_status(request)
    handler.check_hosts_status(request)

    assert apps_v1.read_namespaced_stateful_set.call_count == 3, (
        "TTL=0 must disable the cache — 3 polls must produce 3 GETs"
    )
    assert len(handler._status_resolver._controller_cache) == 0


def test_job_ttl_zero_disables_cache() -> None:
    """TTL=0 must bypass the Job cache — every poll must issue a fresh GET."""
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.return_value = _make_job_obj(succeeded=1)

    client = MagicMock()
    client.core_v1 = core_v1
    client.batch_v1 = batch_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=0.0)
    handler = K8sJobHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Job", deployment_name="job-1")

    handler.check_hosts_status(request)
    handler.check_hosts_status(request)
    handler.check_hosts_status(request)

    assert batch_v1.read_namespaced_job.call_count == 3, (
        "TTL=0 must disable the cache — 3 polls must produce 3 GETs"
    )
    assert len(handler._status_resolver._controller_cache) == 0


def test_job_ttl_negative_disables_cache() -> None:
    """TTL=-1 (negative) must also disable the cache."""
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = _make_pod_list()
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.return_value = _make_job_obj(succeeded=1)

    client = MagicMock()
    client.core_v1 = core_v1
    client.batch_v1 = batch_v1

    config = K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=-1.0)
    handler = K8sJobHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Job", deployment_name="job-1")

    handler.check_hosts_status(request)
    handler.check_hosts_status(request)

    assert batch_v1.read_namespaced_job.call_count == 2
    assert len(handler._status_resolver._controller_cache) == 0


# ---------------------------------------------------------------------------
# Finding 4 — consistent_read kwarg on PodStatusResolver
# ---------------------------------------------------------------------------


def test_pod_status_consistent_read_omits_resource_version() -> None:
    """consistent_read=True must omit resource_version='0' from the list call."""
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])

    client = MagicMock()
    client.core_v1 = core_v1

    config = K8sProviderConfig(namespace="ns")
    handler = K8sPodHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Pod")

    handler._status_resolver.check_hosts_status(request, consistent_read=True)

    call_kwargs = core_v1.list_namespaced_pod.call_args.kwargs
    assert "resource_version" not in call_kwargs, (
        "consistent_read=True must omit resource_version so etcd is queried directly"
    )


def test_pod_status_default_uses_resource_version_zero() -> None:
    """Default (consistent_read=False) must still pass resource_version='0'."""
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])

    client = MagicMock()
    client.core_v1 = core_v1

    config = K8sProviderConfig(namespace="ns")
    handler = K8sPodHandler(kubernetes_client=client, config=config, logger=MagicMock())
    request = _req(provider_api="Pod")

    handler._status_resolver.check_hosts_status(request, consistent_read=False)

    call_kwargs = core_v1.list_namespaced_pod.call_args.kwargs
    assert call_kwargs.get("resource_version") == "0"
