"""Domain events package - Complete event system with domain separation."""

# Base classes and protocols
from .base_events import (
    DomainEvent,
    ErrorEvent,
    EventPublisher,
    InfrastructureEvent,
    OperationEvent,
    PerformanceEvent,
    StatusChangeEvent,
    TimedEvent,
)

# Domain events (Request, Machine, Template)
from .domain_events import (  # Request Events; Machine Events; Template Events
    MachineCreatedEvent,
    MachineEvent,
    MachineHealthCheckEvent,
    MachineProvisionedEvent,
    MachineStatusChangedEvent,
    MachineTerminatedEvent,
    RequestCompletedEvent,
    RequestCreatedEvent,
    RequestEvent,
    RequestFailedEvent,
    RequestStatusChangedEvent,
    RequestTimeoutEvent,
    TemplateCreatedEvent,
    TemplateDeletedEvent,
    TemplateEvent,
    TemplateUpdatedEvent,
    TemplateValidatedEvent,
)

# Provider events (Provider-agnostic)
from .provider_events import (
    ProviderConfigurationEvent,
    ProviderCredentialsEvent,
    ProviderHealthCheckEvent,
    ProviderOperationEvent,
    ProviderRateLimitEvent,
    ProviderResourceStateChangedEvent,
)

# Storage events moved to infrastructure.events.storage_events
# These are infrastructure monitoring events, not domain events
# infrastructure_events and system_events moved to orb.infrastructure.events

# Export all events
__all__: list[str] = [
    # Base classes and protocols
    "DomainEvent",
    "ErrorEvent",
    "EventPublisher",
    "InfrastructureEvent",
    "OperationEvent",
    "PerformanceEvent",
    "StatusChangeEvent",
    "TimedEvent",
    # Machine Events
    "MachineCreatedEvent",
    "MachineEvent",
    "MachineHealthCheckEvent",
    "MachineProvisionedEvent",
    "MachineStatusChangedEvent",
    "MachineTerminatedEvent",
    # Provider Events (Provider-agnostic)
    "ProviderConfigurationEvent",
    "ProviderCredentialsEvent",
    "ProviderHealthCheckEvent",
    "ProviderOperationEvent",
    "ProviderRateLimitEvent",
    "ProviderResourceStateChangedEvent",
    # Request Events
    "RequestCompletedEvent",
    "RequestCreatedEvent",
    "RequestEvent",
    "RequestFailedEvent",
    "RequestStatusChangedEvent",
    "RequestTimeoutEvent",
    # Template Events
    "TemplateCreatedEvent",
    "TemplateDeletedEvent",
    "TemplateEvent",
    "TemplateUpdatedEvent",
    "TemplateValidatedEvent",
]
