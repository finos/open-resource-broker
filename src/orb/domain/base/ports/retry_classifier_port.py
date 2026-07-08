"""Provider-agnostic retry classifier port.

Providers may register a classifier that inspects a raised exception and
returns True when it represents a non-retryable client error (e.g. a 4xx
HTTP status code from the provider's SDK). The resilience layer consults
every registered classifier so retry / circuit-breaker logic can stay
provider-agnostic.
"""

from __future__ import annotations

from typing import Protocol


class RetryClassifierPort(Protocol):
    """Classifies exceptions as retryable or non-retryable.

    Implementations MUST return True when the exception is a permanent
    client error (bad request, RBAC denied, not found, conflict, etc.)
    that must not consume retry budget or count as a circuit failure.

    Implementations MUST return False for any exception they cannot
    classify, so an unknown exception falls through to the next
    classifier or the default retry behaviour.
    """

    def is_non_retryable(self, exception: Exception) -> bool:  # type: ignore[return]
        pass
