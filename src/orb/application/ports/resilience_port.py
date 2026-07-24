"""Application port for circuit-breaker resilience primitives.

Lets the application layer depend on an abstraction for circuit-breaker
behaviour instead of an infrastructure concrete.  Infrastructure supplies a
concrete circuit-breaker factory (wired in the DI bootstrap) that satisfies
:class:`CircuitBreakerPort`, and raises its open-circuit signal as a subclass
of :class:`CircuitBreakerOpenError` so the orchestration layer can catch it
without importing infrastructure.
"""

from typing import Protocol, runtime_checkable


class CircuitBreakerOpenError(Exception):
    """Raised when a circuit breaker is open and failing fast.

    Application-level base that infrastructure circuit-breaker implementations
    subclass.  The orchestration service catches this type so it stays free of
    infrastructure imports; infrastructure adds provider-specific detail fields
    on its concrete subclass.
    """


@runtime_checkable
class CircuitBreakerPort(Protocol):
    """Circuit-breaker operations the application layer relies on.

    A concrete strategy satisfies this port structurally — no explicit
    inheritance required.
    """

    def has_state(self, service_name: str) -> bool:
        """Return True if a circuit state entry exists for ``service_name``."""
        raise NotImplementedError

    def record_success(self) -> None:
        """Record a successful operation, resetting failure state."""

    def record_failure(self, current_time: float) -> None:
        """Record a failed operation, opening the circuit at threshold."""
