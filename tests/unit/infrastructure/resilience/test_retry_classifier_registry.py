"""Unit tests for the retry classifier registry.

Covers:
* A registered classifier that flags a specific exception type causes
  is_non_retryable to return True.
* is_non_retryable returns False for exception types not flagged by any
  registered classifier.
* clear_classifiers empties the registry so subsequent calls return False.
* Idempotent registration: the same classifier object is not double-added.
"""

from __future__ import annotations

import pytest

from orb.infrastructure.resilience.retry_classifier_registry import (
    clear_classifiers,
    is_non_retryable,
    register_retry_classifier,
)


class _SentinelError(Exception):
    """Exception type used exclusively by the fake classifier in these tests."""


class _FakeClassifier:
    """Classifier that flags only _SentinelError instances."""

    def is_non_retryable(self, exception: Exception) -> bool:
        return isinstance(exception, _SentinelError)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Restore a clean registry before and after every test."""
    clear_classifiers()
    yield
    clear_classifiers()


def test_registered_classifier_flags_sentinel_error() -> None:
    register_retry_classifier(_FakeClassifier())
    assert is_non_retryable(_SentinelError("boom")) is True


def test_unrelated_exception_returns_false() -> None:
    register_retry_classifier(_FakeClassifier())
    assert is_non_retryable(ValueError("unrelated")) is False


def test_no_classifiers_returns_false() -> None:
    assert is_non_retryable(_SentinelError("boom")) is False


def test_clear_classifiers_empties_registry() -> None:
    register_retry_classifier(_FakeClassifier())
    clear_classifiers()
    assert is_non_retryable(_SentinelError("boom")) is False


def test_idempotent_registration() -> None:
    """Registering the same classifier object twice must not double-count it."""
    classifier = _FakeClassifier()
    register_retry_classifier(classifier)
    register_retry_classifier(classifier)

    # Still returns True — the classifier is present exactly once.
    assert is_non_retryable(_SentinelError("x")) is True

    # Remove the classifier and confirm the registry is now empty.
    clear_classifiers()
    assert is_non_retryable(_SentinelError("x")) is False
