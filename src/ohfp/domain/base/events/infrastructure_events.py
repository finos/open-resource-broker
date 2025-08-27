"""Infrastructure events - Generic resource and operation tracking."""

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from .base_events import InfrastructureEvent

# =============================================================================
# INFRASTRUCTURE EVENTS
# =============================================================================


class ResourceEvent(InfrastructureEvent):
    """Base class for resource-related infrastructure events."""

    provider: str  # Provider type must be specified
    region: Optional[str] = None


class ResourceCreatedEvent(ResourceEvent):
    """Event raised when an infrastructure resource is created."""

    resource_id: Optional[str] = None  # Generic resource identifier
    creation_time: datetime = Field(default_factory=datetime.utcnow)


class ResourceUpdatedEvent(ResourceEvent):
    """Event raised when an infrastructure resource is updated."""

    changes: dict[str, Any] = Field(default_factory=dict)
    update_time: datetime = Field(default_factory=datetime.utcnow)


class ResourceDeletedEvent(ResourceEvent):
    """Event raised when an infrastructure resource is deleted."""

    deletion_reason: str
    deletion_time: datetime = Field(default_factory=datetime.utcnow)


class ResourcesCleanedEvent(ResourceEvent):
    """Event raised when multiple resources are cleaned up."""

    resource_count: int
    cleanup_reason: str
    cleanup_time: datetime = Field(default_factory=datetime.utcnow)


class ResourceErrorEvent(ResourceEvent):
    """Event raised when there's an error with an infrastructure resource."""

    error_message: str
    error_code: Optional[str] = None
    error_time: datetime = Field(default_factory=datetime.utcnow)


class OperationStartedEvent(InfrastructureEvent):
    """Event raised when an infrastructure operation starts."""

    operation_type: str
    operation_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class OperationCompletedEvent(InfrastructureEvent):
    """Event raised when an infrastructure operation completes."""

    operation_type: str
    operation_id: str
    result: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: Optional[float] = None


class OperationFailedEvent(InfrastructureEvent):
    """Event raised when an infrastructure operation fails."""

    operation_type: str
    operation_id: str
    error_message: str
    error_details: dict[str, Any] = Field(default_factory=dict)
