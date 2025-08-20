"""Base event classes and protocols - foundation for event-driven architecture."""

from datetime import datetime
from typing import Any, Callable, Dict, Optional, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class DomainEvent(BaseModel):
    """Base class for all domain events."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    event_type: str = Field(default="")
    aggregate_id: str
    aggregate_type: str
    version: int = 1
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Set event_type based on class name if not provided."""
        if not self.event_type:
            object.__setattr__(self, "event_type", self.__class__.__name__)


class InfrastructureEvent(DomainEvent):
    """Base class for infrastructure-level events."""

    resource_type: str = ""
    resource_id: str = ""


class TimedEvent(DomainEvent):
    """Base class for events that track duration and timing."""

    duration_ms: float
    start_time: Optional[datetime] = None


class ErrorEvent(DomainEvent):
    """Base class for events that track errors and failures."""

    error_message: str
    error_code: Optional[str] = None
    retry_count: int = 0


class OperationEvent(TimedEvent):
    """Base class for operation events that track success/failure and timing."""

    operation_type: str
    success: bool = True


class PerformanceEvent(TimedEvent):
    """Base class for performance-related events with thresholds."""

    threshold_ms: Optional[float] = None
    threshold_exceeded: bool = False


class StatusChangeEvent(DomainEvent):
    """Base class for events that track status transitions."""

    old_status: str
    new_status: str
    reason: Optional[str] = None


class EventPublisher(Protocol):
    """Protocol for event publishing."""

    def publish(self, event: DomainEvent) -> None:
        """Publish a single domain event."""
        ...

    def register_handler(self, event_type: str, handler: Callable[[DomainEvent], None]) -> None:
        """Register an event handler."""
        ...


class EventHandler(Protocol):
    """Protocol for event handlers."""

    def handle(self, event: DomainEvent) -> None:
        """Handle a domain event."""
        ...
