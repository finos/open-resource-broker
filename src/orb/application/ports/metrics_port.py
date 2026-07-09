"""Port interface for metrics collection used by the application layer."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class MetricsPort(Protocol):
    """Protocol that the application layer uses for metrics collection.

    Any object that implements these 5 methods satisfies this port, including
    ``MetricsCollector`` (monitoring layer).  Using a ``Protocol`` rather than
    an ABC avoids an ``application -> monitoring`` import in ``MetricsCollector``
    that would introduce a ``monitoring -> application`` dependency cycle.

    Concrete implementations live in the monitoring layer (MetricsCollector)
    via structural subtyping — no explicit inheritance required.
    """

    def increment_gauge(self, name: str, delta: float = 1.0) -> None:
        """Increment a gauge metric by delta."""

    def decrement_gauge(self, name: str, delta: float = 1.0) -> None:
        """Decrement a gauge metric by delta."""

    def increment_counter(self, name: str, value: float = 1.0) -> None:
        """Increment a counter metric."""

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge metric value."""

    def record_time(self, name: str, duration: float) -> None:
        """Record a timing duration."""
