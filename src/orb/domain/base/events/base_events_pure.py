"""Pure domain event classes - foundation for event-driven architecture without infrastructure dependencies."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol
from uuid import uuid4


@dataclass(frozen=True)
class DomainEvent:
    """Base class for all domain events - pure Python implementation."""

    aggregate_id: str
    aggregate_type: str
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str = ""
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Set event_type based on class name if not provided."""
        if not self.event_type:
            object.__setattr__(self, "event_type", self.__class__.__name__)


@dataclass(frozen=True)
class InfrastructureEvent(DomainEvent):
    """Base class for infrastructure-level events."""

    resource_type: str = ""
    resource_id: str = ""


@dataclass(frozen=True)
class TimedEvent(DomainEvent):
    """Base class for events that track duration and timing."""

    duration_ms: float = 0.0
    start_time: Optional[datetime] = None


@dataclass(frozen=True)
class ErrorEvent(DomainEvent):
    """Base class for events that track errors and failures."""

    error_message: str = ""
    error_code: Optional[str] = None
    retry_count: int = 0


@dataclass(frozen=True)
class OperationEvent(TimedEvent):
    """Base class for operation events that track success/failure and timing."""

    operation_type: str = ""
    success: bool = True


@dataclass(frozen=True)
class PerformanceEvent(TimedEvent):
    """Base class for performance-related events with thresholds."""

    threshold_ms: Optional[float] = None
    threshold_exceeded: bool = False


@dataclass(frozen=True)
class StatusChangeEvent(DomainEvent):
    """Base class for events that track status transitions."""

    old_status: str = ""
    new_status: str = ""
    reason: Optional[str] = None


class EventPublisher(Protocol):
    """Protocol for event publishing."""

    def publish(self, event: DomainEvent) -> None:
        """Publish a single domain event."""
        ...

    def register_handler(self, event_type: str, handler: Callable[[DomainEvent], None]) -> None:
        """Register an event handler."""
        ...
