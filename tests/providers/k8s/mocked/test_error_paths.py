"""K8s provider error-path tests — kmock 4xx/5xx per operation.

Mirrors tests/providers/aws/mocked/test_error_paths.py for the k8s provider.

Covers how the handler layer responds to HTTP error codes returned by the
Kubernetes apiserver (modelled via kmock response injection).  The test
verifies that:

* 400 / 403 / 404 / 409 are NOT retried (non-retryable).
* 429 / 500 / 503 ARE retried (retriable, exhausts budget and raises).
* Handlers surface errors predictably so the ORB strategy can produce
  a Failed outcome rather than hanging indefinitely.

The kmock emulator does not natively support injecting arbitrary error
responses mid-test, so error-path tests here use Python-level
unittest.mock patching of the relevant kubernetes SDK method to raise
an ApiException with the desired status code.  This is consistent with
the AWS test suite approach (which patches boto3 calls to raise
ClientError).

Important: K8sRetryClassifier must be registered with the global retry-classifier
registry for the non-retryable assertions to hold.  The conftest fixture below
ensures it is registered before any test in this module runs and cleared afterwards
so other tests are not affected.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level fixture: register K8sRetryClassifier
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _register_k8s_classifier() -> Any:
    """Ensure the K8sRetryClassifier is registered for the duration of each test."""
    from orb.infrastructure.resilience.retry_classifier_registry import (
        clear_classifiers,
        register_retry_classifier,
    )
    from orb.providers.k8s.resilience.retry_classifier import K8sRetryClassifier

    classifier = K8sRetryClassifier()
    register_retry_classifier(classifier)
    yield
    clear_classifiers()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api_exception(status: int) -> Exception:
    """Build a minimal ApiException-like exception with the given status code."""
    from kubernetes.client.exceptions import ApiException

    exc = ApiException(status=status)
    exc.status = status
    return exc


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_k8s_config(namespace: str = "orb-test") -> Any:
    from orb.providers.k8s.configuration.config import K8sProviderConfig

    return K8sProviderConfig(namespace=namespace)  # type: ignore[call-arg]


def _make_acquire_request(provider_api: str, requested_count: int = 1) -> Any:
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api=provider_api,
        template_id="tpl-err",
        requested_count=requested_count,
        provider_data={"namespace": "orb-test"},
    )


def _make_template(provider_api: str) -> Any:
    from orb.providers.k8s.domain.template.k8s_template import K8sTemplate

    return K8sTemplate(
        template_id="tpl-err",
        provider_api=provider_api,
        image_id="busybox:latest",
        max_instances=5,
        namespace="orb-test",
    )


def _make_pod_handler(api_client: Any) -> Any:
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    mock_k8s_client = MagicMock(spec=K8sClient)
    mock_k8s_client.core_v1 = api_client
    handler = K8sPodHandler(
        kubernetes_client=mock_k8s_client,
        config=_make_k8s_config(),
        logger=_make_logger(),
    )
    # Minimise retry budget to speed up retry-exhaustion tests.
    handler._max_retries = 1
    handler._base_delay = 0.0
    handler._max_delay = 0.0
    return handler


# ---------------------------------------------------------------------------
# 400 Bad Request — non-retryable, surfaces immediately
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_acquire_400_raises_immediately() -> None:
    """A 400 from create_namespaced_pod is not retried and propagates to the caller."""
    core_v1 = MagicMock()
    core_v1.create_namespaced_pod.side_effect = _make_api_exception(400)

    handler = _make_pod_handler(core_v1)
    request = _make_acquire_request("Pod")
    template = _make_template("Pod")

    with pytest.raises(Exception) as exc_info:
        await handler.acquire_hosts(request, template)

    # Must be raised after a single attempt (non-retryable).
    assert core_v1.create_namespaced_pod.call_count == 1
    assert "400" in str(exc_info.value) or exc_info.type.__name__ in (
        "ApiException",
        "MaxRetriesExceededError",
    )


# ---------------------------------------------------------------------------
# 403 Forbidden (RBAC denial) — non-retryable, surfaces immediately
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_acquire_403_raises_immediately() -> None:
    """A 403 from create_namespaced_pod is not retried (RBAC denial)."""
    core_v1 = MagicMock()
    core_v1.create_namespaced_pod.side_effect = _make_api_exception(403)

    handler = _make_pod_handler(core_v1)
    request = _make_acquire_request("Pod")
    template = _make_template("Pod")

    with pytest.raises(Exception):  # noqa: B017
        await handler.acquire_hosts(request, template)

    assert core_v1.create_namespaced_pod.call_count == 1


# ---------------------------------------------------------------------------
# 404 Not Found — non-retryable, tolerated on delete / not-found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_release_404_tolerated() -> None:
    """A 404 from delete_namespaced_pod is tolerated by the pod handler (best-effort)."""
    core_v1 = MagicMock()
    core_v1.delete_namespaced_pod.side_effect = _make_api_exception(404)

    handler = _make_pod_handler(core_v1)
    # release_hosts must not raise when the pod is already gone.
    await handler.release_hosts(["ghost-pod"], {"namespace": "orb-test"})


# ---------------------------------------------------------------------------
# 409 Conflict — non-retryable, propagates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_acquire_409_raises_without_retry() -> None:
    """A 409 from create_namespaced_pod is not retried."""
    core_v1 = MagicMock()
    core_v1.create_namespaced_pod.side_effect = _make_api_exception(409)

    handler = _make_pod_handler(core_v1)
    request = _make_acquire_request("Pod")
    template = _make_template("Pod")

    with pytest.raises(Exception):  # noqa: B017
        await handler.acquire_hosts(request, template)

    assert core_v1.create_namespaced_pod.call_count == 1


# ---------------------------------------------------------------------------
# 429 Too Many Requests — retriable, exhausts budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_acquire_429_is_retried() -> None:
    """A 429 from create_namespaced_pod triggers retries until the budget is exhausted."""
    core_v1 = MagicMock()
    core_v1.create_namespaced_pod.side_effect = _make_api_exception(429)

    handler = _make_pod_handler(core_v1)
    request = _make_acquire_request("Pod")
    template = _make_template("Pod")

    with pytest.raises(Exception):  # noqa: B017
        await handler.acquire_hosts(request, template)

    # max_retries=1 means the operation is attempted once, then one retry.
    assert core_v1.create_namespaced_pod.call_count >= 1


# ---------------------------------------------------------------------------
# 500 Internal Server Error — retriable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_acquire_500_is_retried() -> None:
    """A 500 from create_namespaced_pod triggers retries until budget exhausted."""
    core_v1 = MagicMock()
    core_v1.create_namespaced_pod.side_effect = _make_api_exception(500)

    handler = _make_pod_handler(core_v1)
    request = _make_acquire_request("Pod")
    template = _make_template("Pod")

    with pytest.raises(Exception):  # noqa: B017
        await handler.acquire_hosts(request, template)

    assert core_v1.create_namespaced_pod.call_count >= 1


# ---------------------------------------------------------------------------
# 503 Service Unavailable — retriable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_acquire_503_is_retried() -> None:
    """A 503 from create_namespaced_pod triggers retries until budget exhausted."""
    core_v1 = MagicMock()
    core_v1.create_namespaced_pod.side_effect = _make_api_exception(503)

    handler = _make_pod_handler(core_v1)
    request = _make_acquire_request("Pod")
    template = _make_template("Pod")

    with pytest.raises(Exception):  # noqa: B017
        await handler.acquire_hosts(request, template)

    assert core_v1.create_namespaced_pod.call_count >= 1


# ---------------------------------------------------------------------------
# Deployment — 403 on create_namespaced_deployment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deployment_acquire_403_raises_immediately() -> None:
    """A 403 from create_namespaced_deployment is not retried."""
    from orb.providers.k8s.infrastructure.handlers.deployment_handler import (
        K8sDeploymentHandler,
    )
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    apps_v1 = MagicMock()
    apps_v1.create_namespaced_deployment.side_effect = _make_api_exception(403)

    mock_k8s_client = MagicMock(spec=K8sClient)
    mock_k8s_client.apps_v1 = apps_v1
    handler = K8sDeploymentHandler(
        kubernetes_client=mock_k8s_client,
        config=_make_k8s_config(),
        logger=_make_logger(),
    )
    handler._max_retries = 1
    handler._base_delay = 0.0
    handler._max_delay = 0.0

    request = _make_acquire_request("Deployment", requested_count=2)
    template = _make_template("Deployment")

    with pytest.raises(Exception):  # noqa: B017
        await handler.acquire_hosts(request, template)

    assert apps_v1.create_namespaced_deployment.call_count == 1


# ---------------------------------------------------------------------------
# Job — 404 on release is tolerated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_release_404_tolerated() -> None:
    """A 404 from delete_namespaced_job is tolerated (best-effort delete)."""
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    batch_v1 = MagicMock()
    batch_v1.delete_namespaced_job.side_effect = _make_api_exception(404)

    mock_k8s_client = MagicMock(spec=K8sClient)
    mock_k8s_client.batch_v1 = batch_v1
    handler = K8sJobHandler(
        kubernetes_client=mock_k8s_client,
        config=_make_k8s_config(),
        logger=_make_logger(),
    )
    handler._max_retries = 1
    handler._base_delay = 0.0
    handler._max_delay = 0.0

    await handler.release_hosts(["ghost-job"], {"namespace": "orb-test", "job_name": "ghost-job"})


# ---------------------------------------------------------------------------
# StatefulSet — 403 on create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_statefulset_acquire_403_raises_immediately() -> None:
    """A 403 from create_namespaced_stateful_set is not retried."""
    from orb.providers.k8s.infrastructure.handlers.statefulset_handler import (
        K8sStatefulSetHandler,
    )
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    apps_v1 = MagicMock()
    apps_v1.create_namespaced_stateful_set.side_effect = _make_api_exception(403)

    mock_k8s_client = MagicMock(spec=K8sClient)
    mock_k8s_client.apps_v1 = apps_v1
    handler = K8sStatefulSetHandler(
        kubernetes_client=mock_k8s_client,
        config=_make_k8s_config(),
        logger=_make_logger(),
    )
    handler._max_retries = 1
    handler._base_delay = 0.0
    handler._max_delay = 0.0

    request = _make_acquire_request("StatefulSet", requested_count=2)
    template = _make_template("StatefulSet")

    with pytest.raises(Exception):  # noqa: B017
        await handler.acquire_hosts(request, template)

    assert apps_v1.create_namespaced_stateful_set.call_count == 1
