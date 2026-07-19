"""Status-resolver metrics wiring tests.

Every status-resolver read/list path must:

* Record ``orb_k8s_apiserver_latency_seconds`` for the API call (via the
  handler's ``_timed_api_call`` context manager), and
* Record ``orb_k8s_api_errors_total`` when the call fails (the resolver
  swallows the exception and returns an in_progress / empty verdict, so the
  error would otherwise be invisible on dashboards).

The four resolvers (Pod, Deployment, StatefulSet, Job) are exercised through
their concrete handlers with a MagicMock ``K8sMetrics`` injected so the record_*
calls can be asserted directly.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.instrumentation.metrics import K8sMetrics


def _req(*, provider_api: str, name: str = "wl-1", namespace: str = "ns") -> Request:
    pd: dict[str, Any] = {
        "namespace": namespace,
        "deployment_name": name,
        "statefulset_name": name,
        "job_name": name,
    }
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api=provider_api,
        template_id="tpl-1",
        requested_count=1,
        provider_data=pd,
    )


def _pod_list() -> SimpleNamespace:
    return SimpleNamespace(
        items=[
            SimpleNamespace(
                metadata=SimpleNamespace(
                    name="pod-1", namespace="ns", labels={"orb.io/request-id": "req-x"}
                ),
                spec=SimpleNamespace(node_name="node-1"),
                status=SimpleNamespace(
                    phase="Running",
                    pod_ip="10.0.0.1",
                    host_ip="10.1.0.1",
                    start_time=None,
                    conditions=[SimpleNamespace(type="Ready", status="True", reason=None)],
                    container_statuses=[],
                ),
            )
        ]
    )


def _make_client() -> Any:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    batch_v1 = MagicMock()
    client = MagicMock()
    client.core_v1 = core_v1
    client.apps_v1 = apps_v1
    client.batch_v1 = batch_v1
    return client


def _config() -> K8sProviderConfig:
    # TTL 0 disables the controller cache so the read GET always fires.
    return K8sProviderConfig(namespace="ns", controller_status_cache_ttl_seconds=0.0)


# Retry tuning passed to every handler so failing calls exhaust the budget
# quickly (no backoff sleeps) during the error-path tests.
_RETRY_KW: dict[str, Any] = {"max_retries": 1, "base_delay": 0.0, "max_delay": 0.0}


@pytest.fixture(autouse=True)
def _register_k8s_classifier():
    from orb.infrastructure.resilience.retry_classifier_registry import (
        clear_classifiers,
        register_retry_classifier,
    )
    from orb.providers.k8s.resilience.retry_classifier import K8sRetryClassifier

    register_retry_classifier(K8sRetryClassifier())
    yield
    clear_classifiers()


# ---------------------------------------------------------------------------
# Pod resolver
# ---------------------------------------------------------------------------


def test_pod_status_records_latency_on_success() -> None:
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

    client = _make_client()
    client.core_v1.list_namespaced_pod.return_value = _pod_list()
    metrics = MagicMock(spec=K8sMetrics)
    handler = K8sPodHandler(
        kubernetes_client=client,
        config=_config(),
        logger=MagicMock(),
        metrics=metrics,
        **_RETRY_KW,
    )

    handler.check_hosts_status(_req(provider_api="Pod"))

    ops = [c.kwargs.get("operation") for c in metrics.record_apiserver_latency.call_args_list]
    assert "list_namespaced_pod" in ops
    metrics.record_api_error.assert_not_called()


def test_pod_status_records_error_on_list_failure() -> None:
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

    client = _make_client()
    client.core_v1.list_namespaced_pod.side_effect = ApiException(status=500, reason="boom")
    metrics = MagicMock(spec=K8sMetrics)
    handler = K8sPodHandler(
        kubernetes_client=client,
        config=_config(),
        logger=MagicMock(),
        metrics=metrics,
        **_RETRY_KW,
    )

    result = handler.check_hosts_status(_req(provider_api="Pod"))

    # Resolver swallows and returns in_progress, but the error must be recorded.
    assert result.fulfilment.state == "in_progress"
    metrics.record_api_error.assert_any_call(operation="list_namespaced_pod", error_code="500")
    # Latency still recorded (timed context exits on exception).
    ops = [c.kwargs.get("operation") for c in metrics.record_apiserver_latency.call_args_list]
    assert "list_namespaced_pod" in ops


# ---------------------------------------------------------------------------
# Deployment resolver
# ---------------------------------------------------------------------------


def test_deployment_status_records_error_on_read_failure() -> None:
    from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler

    client = _make_client()
    client.core_v1.list_namespaced_pod.return_value = _pod_list()
    client.apps_v1.read_namespaced_deployment.side_effect = ApiException(
        status=503, reason="unavailable"
    )
    metrics = MagicMock(spec=K8sMetrics)
    handler = K8sDeploymentHandler(
        kubernetes_client=client,
        config=_config(),
        logger=MagicMock(),
        metrics=metrics,
        **_RETRY_KW,
    )

    handler.check_hosts_status(_req(provider_api="Deployment"))

    metrics.record_api_error.assert_any_call(
        operation="read_namespaced_deployment", error_code="503"
    )
    ops = [c.kwargs.get("operation") for c in metrics.record_apiserver_latency.call_args_list]
    assert "read_namespaced_deployment" in ops


def test_deployment_status_404_read_does_not_record_error() -> None:
    from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler

    client = _make_client()
    client.core_v1.list_namespaced_pod.return_value = _pod_list()
    client.apps_v1.read_namespaced_deployment.side_effect = ApiException(
        status=404, reason="not found"
    )
    metrics = MagicMock(spec=K8sMetrics)
    handler = K8sDeploymentHandler(
        kubernetes_client=client,
        config=_config(),
        logger=MagicMock(),
        metrics=metrics,
        **_RETRY_KW,
    )

    handler.check_hosts_status(_req(provider_api="Deployment"))

    # 404 on read is a normal pre-create / post-release signal, not an error.
    for call in metrics.record_api_error.call_args_list:
        assert call.kwargs.get("operation") != "read_namespaced_deployment"


# ---------------------------------------------------------------------------
# StatefulSet resolver
# ---------------------------------------------------------------------------


def test_statefulset_status_records_error_on_read_failure() -> None:
    from orb.providers.k8s.infrastructure.handlers.statefulset_handler import K8sStatefulSetHandler

    client = _make_client()
    client.core_v1.list_namespaced_pod.return_value = _pod_list()
    client.apps_v1.read_namespaced_stateful_set.side_effect = ApiException(
        status=500, reason="boom"
    )
    metrics = MagicMock(spec=K8sMetrics)
    handler = K8sStatefulSetHandler(
        kubernetes_client=client,
        config=_config(),
        logger=MagicMock(),
        metrics=metrics,
        **_RETRY_KW,
    )

    handler.check_hosts_status(_req(provider_api="StatefulSet"))

    metrics.record_api_error.assert_any_call(
        operation="read_namespaced_stateful_set", error_code="500"
    )
    ops = [c.kwargs.get("operation") for c in metrics.record_apiserver_latency.call_args_list]
    assert "read_namespaced_stateful_set" in ops


# ---------------------------------------------------------------------------
# Job resolver
# ---------------------------------------------------------------------------


def test_job_status_records_error_on_read_failure() -> None:
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler

    client = _make_client()
    client.core_v1.list_namespaced_pod.return_value = _pod_list()
    client.batch_v1.read_namespaced_job.side_effect = ApiException(status=500, reason="boom")
    metrics = MagicMock(spec=K8sMetrics)
    handler = K8sJobHandler(
        kubernetes_client=client,
        config=_config(),
        logger=MagicMock(),
        metrics=metrics,
        **_RETRY_KW,
    )

    handler.check_hosts_status(_req(provider_api="Job"))

    metrics.record_api_error.assert_any_call(operation="read_namespaced_job", error_code="500")
    ops = [c.kwargs.get("operation") for c in metrics.record_apiserver_latency.call_args_list]
    assert "read_namespaced_job" in ops
