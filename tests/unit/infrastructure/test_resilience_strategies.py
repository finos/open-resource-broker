"""Unit tests for resilience strategies.

Covers:
* ExponentialBackoffStrategy.should_retry fast-fails on non-retryable
  Kubernetes ApiException status codes (400, 403, 404, 409, 410, 422) when
  the K8sRetryClassifier is registered.
* 5xx and transient errors are still retried.
"""

from __future__ import annotations

import pytest
from kubernetes.client.exceptions import ApiException

from orb.infrastructure.resilience.retry_classifier_registry import (
    clear_classifiers,
    register_retry_classifier,
)
from orb.infrastructure.resilience.strategy.exponential import ExponentialBackoffStrategy
from orb.providers.k8s.resilience.retry_classifier import K8sRetryClassifier


@pytest.fixture(autouse=True)
def _register_k8s_classifier():
    """Register the K8s classifier for tests in this module, then clean up."""
    register_retry_classifier(K8sRetryClassifier())
    yield
    clear_classifiers()


# ---------------------------------------------------------------------------
# ExponentialBackoffStrategy — non-retryable k8s status codes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status_code",
    [400, 403, 404, 409, 410, 422],
)
def test_non_retryable_k8s_status_codes_are_not_retried(status_code: int) -> None:
    """ApiException with a non-retryable status must cause should_retry to return False."""
    strategy = ExponentialBackoffStrategy(max_attempts=3)
    exc = ApiException(status=status_code, reason=f"HTTP {status_code}")
    # attempt=0 still has budget remaining; the status code alone must block retry.
    result = strategy.should_retry(attempt=0, exception=exc)
    assert result is False, (
        f"status={status_code} should not be retried but should_retry returned True"
    )


@pytest.mark.parametrize(
    "status_code",
    [400, 409],
)
def test_409_and_400_not_retried(status_code: int) -> None:
    """Concrete verification for the two most common idempotency conflict codes."""
    strategy = ExponentialBackoffStrategy(max_attempts=5)
    exc = ApiException(status=status_code)
    assert strategy.should_retry(attempt=0, exception=exc) is False


@pytest.mark.parametrize(
    "status_code",
    [500, 502, 503, 429],
)
def test_retryable_status_codes_are_retried(status_code: int) -> None:
    """ApiException with a transient server-side status must still be retried."""
    strategy = ExponentialBackoffStrategy(max_attempts=3)
    exc = ApiException(status=status_code, reason=f"HTTP {status_code}")
    result = strategy.should_retry(attempt=0, exception=exc)
    assert result is True, f"status={status_code} should be retried but should_retry returned False"


def test_non_k8s_exception_is_retried() -> None:
    """Generic (non-ApiException) exceptions must still be retried as before."""
    strategy = ExponentialBackoffStrategy(max_attempts=3)
    assert strategy.should_retry(attempt=0, exception=RuntimeError("boom")) is True
    assert strategy.should_retry(attempt=0, exception=ConnectionError("timeout")) is True


def test_max_attempts_exceeded_returns_false_regardless_of_status() -> None:
    """Budget exhaustion must take precedence over status filtering."""
    strategy = ExponentialBackoffStrategy(max_attempts=3)
    # attempt == max_attempts means budget is exhausted.
    assert strategy.should_retry(attempt=3, exception=ApiException(status=500)) is False
    assert strategy.should_retry(attempt=3, exception=RuntimeError("boom")) is False
