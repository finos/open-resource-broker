"""Registry of provider-supplied retry classifiers.

Populated by provider registration modules at startup. The resilience
strategies (circuit-breaker, exponential backoff) consult every registered
classifier via short-circuit OR: if any returns True the exception is
treated as non-retryable.
"""

from __future__ import annotations

from orb.domain.base.exceptions import OperationNotSupportedError, ProviderNotReadyError
from orb.domain.base.ports.retry_classifier_port import RetryClassifierPort


class _CoreClassifier:
    """Flags the provider-gate errors as permanent for the caller's retry loop.

    A provider that is not ready or does not support an operation will not
    change its answer by being asked again, so these must neither consume
    retry budget nor count as circuit failures.
    """

    def is_non_retryable(self, exception: Exception) -> bool:
        return isinstance(exception, (ProviderNotReadyError, OperationNotSupportedError))


# Built in at import time so it applies whichever providers are loaded and is
# unaffected by ``clear_classifiers``.
_BUILTIN_CLASSIFIERS: tuple[RetryClassifierPort, ...] = (_CoreClassifier(),)

_classifiers: list[RetryClassifierPort] = []


def register_retry_classifier(classifier: RetryClassifierPort) -> None:
    """Register a classifier. Idempotent by identity."""
    if classifier not in _classifiers:
        _classifiers.append(classifier)


def is_non_retryable(exception: Exception) -> bool:
    """Return True when any registered classifier flags the exception."""
    return any(c.is_non_retryable(exception) for c in (*_BUILTIN_CLASSIFIERS, *_classifiers))


def clear_classifiers() -> None:
    """Test-only helper: drop every provider-registered classifier (built-ins stay)."""
    _classifiers.clear()
