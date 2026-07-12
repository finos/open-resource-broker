"""Regression tests for Pod handler 409-on-retry idempotency (Fix 6).

When a retried ``create_namespaced_pod`` receives 409 AlreadyExists and
the existing pod carries the same ``orb.io/request-id`` label as the pod
being submitted, the create is treated as a success (the pod is already
ours).  A 409 on a pod owned by a different request is still an error.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from orb.infrastructure.resilience.retry_classifier_registry import (
    clear_classifiers,
    register_retry_classifier,
)
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler
from orb.providers.k8s.resilience.retry_classifier import K8sRetryClassifier


@pytest.fixture(autouse=True)
def _register_k8s_classifier():
    """Register K8sRetryClassifier so that 409 is non-retryable in tests.

    Without this, with_retry retries 409 three times and wraps the
    ApiException in MaxRetriesExceededError, which our status==409 check
    does not see.  The classifier is registered during real provider
    registration; tests must register it explicitly.
    """
    register_retry_classifier(K8sRetryClassifier())
    yield
    clear_classifiers()


def _make_handler() -> K8sPodHandler:
    config = K8sProviderConfig(namespace="orb-test")
    client = MagicMock()
    return K8sPodHandler(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
    )


def _make_body(*, request_id: str, pod_name: str, label_prefix: str = "orb.io") -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": "orb-test",
            "labels": {
                f"{label_prefix}/request-id": request_id,
                f"{label_prefix}/managed": "true",
            },
        },
    }


def _make_existing_pod(*, request_id: str, pod_name: str, label_prefix: str = "orb.io") -> Any:
    metadata = MagicMock()
    metadata.labels = {
        f"{label_prefix}/request-id": request_id,
        f"{label_prefix}/managed": "true",
    }
    pod = MagicMock()
    pod.metadata = metadata
    return pod


# ---------------------------------------------------------------------------
# 409 is treated as idempotent when existing pod belongs to our request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_409_idempotent_when_pod_belongs_to_our_request() -> None:
    """409 AlreadyExists + existing pod has our request-id → treated as success."""
    request_id = f"req-{uuid.uuid4()}"
    pod_name = "orb-test-pod-0001"

    handler = _make_handler()
    # Simulate create raising 409.
    handler.client.core_v1.create_namespaced_pod.side_effect = ApiException(  # type: ignore[attr-defined]
        status=409, reason="AlreadyExists"
    )
    # Simulate read returning a pod with our request-id.
    existing_pod = _make_existing_pod(request_id=request_id, pod_name=pod_name)
    handler.client.core_v1.read_namespaced_pod.return_value = existing_pod  # type: ignore[attr-defined]

    body = _make_body(request_id=request_id, pod_name=pod_name)

    sem = asyncio.Semaphore(10)
    result = await handler._create_one_pod(  # type: ignore[attr-defined]
        sem=sem, namespace="orb-test", pod_name=pod_name, body=body
    )

    assert result == pod_name, "Idempotent 409 must return the pod name as success"


@pytest.mark.asyncio
async def test_409_is_failure_when_pod_belongs_to_different_request() -> None:
    """409 AlreadyExists + existing pod has a different request-id → failure."""
    our_request_id = f"req-{uuid.uuid4()}"
    their_request_id = f"req-{uuid.uuid4()}"
    pod_name = "orb-test-pod-0001"

    handler = _make_handler()
    handler.client.core_v1.create_namespaced_pod.side_effect = ApiException(  # type: ignore[attr-defined]
        status=409, reason="AlreadyExists"
    )
    # Existing pod belongs to a different request.
    existing_pod = _make_existing_pod(request_id=their_request_id, pod_name=pod_name)
    handler.client.core_v1.read_namespaced_pod.return_value = existing_pod  # type: ignore[attr-defined]

    body = _make_body(request_id=our_request_id, pod_name=pod_name)

    sem = asyncio.Semaphore(10)
    with pytest.raises(Exception):
        await handler._create_one_pod(  # type: ignore[attr-defined]
            sem=sem, namespace="orb-test", pod_name=pod_name, body=body
        )


@pytest.mark.asyncio
async def test_409_is_failure_when_existing_pod_read_fails() -> None:
    """409 + read failure → treated as conflict (safe default)."""
    request_id = f"req-{uuid.uuid4()}"
    pod_name = "orb-test-pod-0001"

    handler = _make_handler()
    handler.client.core_v1.create_namespaced_pod.side_effect = ApiException(  # type: ignore[attr-defined]
        status=409, reason="AlreadyExists"
    )
    handler.client.core_v1.read_namespaced_pod.side_effect = Exception("network error")  # type: ignore[attr-defined]

    body = _make_body(request_id=request_id, pod_name=pod_name)

    sem = asyncio.Semaphore(10)
    with pytest.raises(Exception):
        await handler._create_one_pod(  # type: ignore[attr-defined]
            sem=sem, namespace="orb-test", pod_name=pod_name, body=body
        )


__all__: list[str] = []
