"""Base domain layer - shared kernel for all bounded contexts."""

from orb.infrastructure.base.exceptions import InfrastructureError

from .domain_interfaces import (
    AggregateRepository,
    Repository,
    RepositoryProtocol,
    UnitOfWork,
    UnitOfWorkFactory,
)
from .entity import AggregateRoot, Entity
from .events import (  # Request Events; Machine Events; Template Events
    DomainEvent,
    EventPublisher,
    InfrastructureEvent,
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
from .exceptions import (
    BusinessRuleViolationError,
    ConcurrencyError,
    ConfigurationError,
    DomainException,
    EntityNotFoundError,
    InvariantViolationError,
    ValidationError,
)
from .value_objects import (
    ARN,
    InstanceType,
    IPAddress,
    PriceType,
    ResourceId,
    Tags,
    ValueObject,
)

__all__: list[str] = [
    "ARN",
    "AggregateRepository",
    "AggregateRoot",
    "BusinessRuleViolationError",
    "ConcurrencyError",
    "ConfigurationError",
    # Events
    "DomainEvent",
    "InfrastructureEvent",
    "MachineCreatedEvent",
    "MachineEvent",
    "MachineHealthCheckEvent",
    "MachineProvisionedEvent",
    "MachineStatusChangedEvent",
    "MachineTerminatedEvent",
    "RequestCompletedEvent",
    "RequestCreatedEvent",
    "RequestEvent",
    "RequestFailedEvent",
    "RequestStatusChangedEvent",
    "RequestTimeoutEvent",
    "TemplateCreatedEvent",
    "TemplateDeletedEvent",
    "TemplateEvent",
    "TemplateUpdatedEvent",
    "TemplateValidatedEvent",
    # Exceptions
    "DomainException",
    # Entities
    "Entity",
    "EntityNotFoundError",
    "EventPublisher",
    "IPAddress",
    "InfrastructureError",
    "InstanceType",
    "InvariantViolationError",
    "PriceType",
    # Repository
    "Repository",
    # Domain Interfaces (clean)
    "RepositoryProtocol",
    "ResourceId",
    "Tags",
    "UnitOfWork",
    "UnitOfWorkFactory",
    "ValidationError",
    # Value Objects
    "ValueObject",
]
