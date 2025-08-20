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
    # Base classes and protocols
    "DomainEvent",
    "InfrastructureEvent",
    "EventPublisher",
    "TimedEvent",
    "ErrorEvent",
    "OperationEvent",
    "PerformanceEvent",
    "StatusChangeEvent",
    # Request Events
    "RequestEvent",
    "RequestCreatedEvent",
    "RequestStatusChangedEvent",
    "RequestCompletedEvent",
    "RequestFailedEvent",
    "RequestTimeoutEvent",
    # Machine Events
    "MachineEvent",
    "MachineCreatedEvent",
    "MachineStatusChangedEvent",
    "MachineProvisionedEvent",
    "MachineTerminatedEvent",
    "MachineHealthCheckEvent",
    # Template Events
    "TemplateEvent",
    "TemplateCreatedEvent",
    "TemplateValidatedEvent",
    "TemplateUpdatedEvent",
    "TemplateDeletedEvent",
    # Infrastructure Events
    "ResourceEvent",
    "ResourceCreatedEvent",
    "ResourceUpdatedEvent",
    "ResourceDeletedEvent",
    "ResourceErrorEvent",
    "OperationStartedEvent",
    "OperationCompletedEvent",
    "OperationFailedEvent",
    # Repository Operation Events
    "PersistenceEvent",
    "RepositoryOperationStartedEvent",
    "RepositoryOperationCompletedEvent",
    "RepositoryOperationFailedEvent",
    "SlowQueryDetectedEvent",
    "TransactionStartedEvent",
    "TransactionCommittedEvent",
    # Storage Strategy Events
    "StorageEvent",
    "StorageStrategySelectedEvent",
    "StorageStrategyFailoverEvent",
    "ConnectionPoolEvent",
    "StoragePerformanceEvent",
    "StorageHealthCheckEvent",
    # System Events
    "SystemEvent",
    "ConfigurationLoadedEvent",
    "ConfigurationChangedEvent",
    "ConfigurationErrorEvent",
    "ApplicationStartedEvent",
    "ApplicationShutdownEvent",
    "ApplicationErrorEvent",
    "SecurityEvent",
    "AuditTrailEvent",
    "ComplianceEvent",
    "PerformanceMetricEvent",
    "HealthCheckEvent",
    # Provider Events (Provider-agnostic)
    "ProviderOperationEvent",
    "ProviderRateLimitEvent",
    "ProviderCredentialsEvent",
    "ProviderResourceStateChangedEvent",
    "ProviderConfigurationEvent",
    "ProviderHealthCheckEvent",
]
