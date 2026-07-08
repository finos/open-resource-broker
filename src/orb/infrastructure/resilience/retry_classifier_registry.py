"""Registry of provider-supplied retry classifiers.

Populated by provider registration modules at startup. The resilience
strategies (circuit-breaker, exponential backoff) consult every registered
classifier via short-circuit OR: if any returns True the exception is
treated as non-retryable.
"""

from __future__ import annotations

from orb.domain.base.ports.retry_classifier_port import RetryClassifierPort

_classifiers: list[RetryClassifierPort] = []


def register_retry_classifier(classifier: RetryClassifierPort) -> None:
    """Register a classifier. Idempotent by identity."""
    if classifier not in _classifiers:
        _classifiers.append(classifier)


def is_non_retryable(exception: Exception) -> bool:
    """Return True when any registered classifier flags the exception."""
    return any(c.is_non_retryable(exception) for c in _classifiers)


def clear_classifiers() -> None:
    """Test-only helper: drop every registered classifier."""
    _classifiers.clear()
