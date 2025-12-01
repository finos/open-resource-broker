"""Domain events - Request, Machine, and Template business events."""

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field

from .base_events import DomainEvent, StatusChangeEvent

# =============================================================================
# REQUEST DOMAIN EVENTS
# =============================================================================


class RequestEvent(DomainEvent):
    """Base class for request-related events."""

    request_id: str
    request_type: str


class RequestCreatedEvent(RequestEvent):
    """Event raised when a request is created."""

    template_id: str
    machine_count: int
    timeout: Optional[int] = None
    tags: dict[str, str] = Field(default_factory=dict)


class RequestStatusChangedEvent(RequestEvent, StatusChangeEvent):
    """Event raised when request status changes."""

    pass  # All fields inherited from StatusChangeEvent


class RequestCompletedEvent(RequestEvent):
    """Event raised when a request is completed."""

    completion_status: str
    machine_ids: list[str] = Field(default_factory=list)
    completion_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RequestFailedEvent(RequestEvent):
    """Event raised when a request fails."""

    error_message: str
    error_code: Optional[str] = None
    failure_reason: str


class RequestTimeoutEvent(RequestEvent):
    """Event raised when a request times out."""

    timeout_duration: int
    partial_results: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# MACHINE DOMAIN EVENTS
# =============================================================================


class MachineEvent(DomainEvent):
    """Base class for machine-related events."""

    machine_id: str
    instance_id: Optional[str] = None


class MachineCreatedEvent(MachineEvent):
    """Event raised when a machine is created."""

    template_id: str
    instance_type: str
    availability_zone: Optional[str] = None


class MachineStatusChangedEvent(MachineEvent, StatusChangeEvent):
    """Event raised when machine status changes."""

    health_status: Optional[str] = None


class MachineProvisionedEvent(MachineEvent):
    """Event raised when a machine is successfully provisioned."""

    instance_id: str
    private_ip: Optional[str] = None
    public_ip: Optional[str] = None
    provisioning_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MachineTerminatedEvent(MachineEvent):
    """Event raised when a machine is terminated."""

    termination_reason: str
    termination_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MachineHealthCheckEvent(MachineEvent):
    """Event raised during machine health checks."""

    health_status: str
    health_details: dict[str, Any] = Field(default_factory=dict)
    check_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# TEMPLATE DOMAIN EVENTS
# =============================================================================


class TemplateEvent(DomainEvent):
    """Base class for template-related events."""

    template_id: str
    template_name: str


class TemplateCreatedEvent(TemplateEvent):
    """Event raised when a template is created."""

    template_type: str
    configuration: dict[str, Any] = Field(default_factory=dict)


class TemplateValidatedEvent(TemplateEvent):
    """Event raised when a template is validated."""

    validation_result: str
    validation_details: dict[str, Any] = Field(default_factory=dict)


class TemplateUpdatedEvent(TemplateEvent):
    """Event raised when a template is updated."""

    changes: dict[str, Any] = Field(default_factory=dict)
    version: int


class TemplateDeletedEvent(TemplateEvent):
    """Event raised when a template is deleted."""

    deletion_reason: str
    deletion_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
