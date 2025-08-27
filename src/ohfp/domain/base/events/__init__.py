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

# Infrastructure events (Provider resources and operations)
from .infrastructure_events import (
    OperationCompletedEvent,
    OperationFailedEvent,
    OperationStartedEvent,
    ResourceCreatedEvent,
    ResourceDeletedEvent,
    ResourceErrorEvent,
    ResourceEvent,
    ResourceUpdatedEvent,
)

# Persistence events (Repository and storage)
from .persistence_events import (  # Repository operations; Storage strategy
    ConnectionPoolEvent,
    PersistenceEvent,
    RepositoryOperationCompletedEvent,
    RepositoryOperationFailedEvent,
    RepositoryOperationStartedEvent,
    SlowQueryDetectedEvent,
    StorageEvent,
    StorageHealthCheckEvent,
    StoragePerformanceEvent,
    StorageStrategyFailoverEvent,
    StorageStrategySelectedEvent,
    TransactionCommittedEvent,
    TransactionStartedEvent,
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

# System events (Configuration, lifecycle, security, performance)
from .system_events import (  # System base; Configuration events; Application lifecycle events; Security and audit events; Performance and monitoring events
    ApplicationErrorEvent,
    ApplicationShutdownEvent,
    ApplicationStartedEvent,
    AuditTrailEvent,
    ComplianceEvent,
    ConfigurationChangedEvent,
    ConfigurationErrorEvent,
    ConfigurationLoadedEvent,
    HealthCheckEvent,
    PerformanceMetricEvent,
    SecurityEvent,
    SystemEvent,
)

# Export all events
__all__: list[str] = [
    "ApplicationErrorEvent",
    "ApplicationShutdownEvent",
    "ApplicationStartedEvent",
    "AuditTrailEvent",
    "ComplianceEvent",
    "ConfigurationChangedEvent",
    "ConfigurationErrorEvent",
    "ConfigurationLoadedEvent",
    "ConnectionPoolEvent",
    # Base classes and protocols
    "DomainEvent",
    "ErrorEvent",
    "EventPublisher",
    "HealthCheckEvent",
    "InfrastructureEvent",
    "MachineCreatedEvent",
    # Machine Events
    "MachineEvent",
    "MachineHealthCheckEvent",
    "MachineProvisionedEvent",
    "MachineStatusChangedEvent",
    "MachineTerminatedEvent",
    "OperationCompletedEvent",
    "OperationEvent",
    "OperationFailedEvent",
    "OperationStartedEvent",
    "PerformanceEvent",
    "PerformanceMetricEvent",
    # Repository Operation Events
    "PersistenceEvent",
    "ProviderConfigurationEvent",
    "ProviderCredentialsEvent",
    "ProviderHealthCheckEvent",
    # Provider Events (Provider-agnostic)
    "ProviderOperationEvent",
    "ProviderRateLimitEvent",
    "ProviderResourceStateChangedEvent",
    "RepositoryOperationCompletedEvent",
    "RepositoryOperationFailedEvent",
    "RepositoryOperationStartedEvent",
    "RequestCompletedEvent",
    "RequestCreatedEvent",
    # Request Events
    "RequestEvent",
    "RequestFailedEvent",
    "RequestStatusChangedEvent",
    "RequestTimeoutEvent",
    "ResourceCreatedEvent",
    "ResourceDeletedEvent",
    "ResourceErrorEvent",
    # Infrastructure Events
    "ResourceEvent",
    "ResourceUpdatedEvent",
    "SecurityEvent",
    "SlowQueryDetectedEvent",
    "StatusChangeEvent",
    # Storage Strategy Events
    "StorageEvent",
    "StorageHealthCheckEvent",
    "StoragePerformanceEvent",
    "StorageStrategyFailoverEvent",
    "StorageStrategySelectedEvent",
    # System Events
    "SystemEvent",
    "TemplateCreatedEvent",
    "TemplateDeletedEvent",
    # Template Events
    "TemplateEvent",
    "TemplateUpdatedEvent",
    "TemplateValidatedEvent",
    "TimedEvent",
    "TransactionCommittedEvent",
    "TransactionStartedEvent",
]
