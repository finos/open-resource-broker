"""Unit tests for K8sRetryClassifier.

Covers:
* is_non_retryable returns True for ApiException with a non-retryable status
  code (400, 403, 404, 409, 410, 422).
* is_non_retryable returns False for ApiException with a retryable server-side
  status code (500).
* is_non_retryable returns False for exceptions that are not ApiException
  (e.g. ValueError, RuntimeError).
"""

from __future__ import annotations

import pytest
from kubernetes.client.exceptions import ApiException

from orb.providers.k8s.resilience.retry_classifier import K8sRetryClassifier


@pytest.fixture()
def classifier() -> K8sRetryClassifier:
    return K8sRetryClassifier()


@pytest.mark.parametrize("status_code", [400, 403, 404, 409, 410, 422])
def test_non_retryable_status_codes_flagged(
    classifier: K8sRetryClassifier, status_code: int
) -> None:
    exc = ApiException(status=status_code, reason=f"HTTP {status_code}")
    assert classifier.is_non_retryable(exc) is True, (
        f"Expected is_non_retryable=True for status={status_code}"
    )


def test_500_is_retryable(classifier: K8sRetryClassifier) -> None:
    exc = ApiException(status=500, reason="Internal Server Error")
    assert classifier.is_non_retryable(exc) is False


@pytest.mark.parametrize("status_code", [500, 502, 503, 429])
def test_server_side_status_codes_are_retryable(
    classifier: K8sRetryClassifier, status_code: int
) -> None:
    exc = ApiException(status=status_code, reason=f"HTTP {status_code}")
    assert classifier.is_non_retryable(exc) is False


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bad input"),
        RuntimeError("unexpected"),
        ConnectionError("network failure"),
    ],
)
def test_non_api_exception_returns_false(classifier: K8sRetryClassifier, exc: Exception) -> None:
    assert classifier.is_non_retryable(exc) is False
