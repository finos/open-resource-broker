"""Base domain layer - shared kernel for all bounded contexts."""

from .domain_interfaces import (
    AggregateRepository,
    Repository,
    RepositoryProtocol,
    UnitOfWork,
    UnitOfWorkFactory,
)
from .entity import AggregateRoot, Entity
from .events import (  # Request Events; Machine Events; Template Events; Infrastructure Events
    DomainEvent,
    EventPublisher,
    InfrastructureEvent,
    MachineCreatedEvent,
    MachineEvent,
    MachineHealthCheckEvent,
    MachineProvisionedEvent,
    MachineStatusChangedEvent,
    MachineTerminatedEvent,
    OperationCompletedEvent,
    OperationFailedEvent,
    OperationStartedEvent,
    RequestCompletedEvent,
    RequestCreatedEvent,
    RequestEvent,
    RequestFailedEvent,
    RequestStatusChangedEvent,
    RequestTimeoutEvent,
    ResourceCreatedEvent,
    ResourceDeletedEvent,
    ResourceErrorEvent,
    ResourceEvent,
    ResourceUpdatedEvent,
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
    InfrastructureError,
    InvariantViolationError,
    ValidationError,
)
from .value_objects import (
    ARN,
    AllocationStrategy,
    InstanceId,
    InstanceType,
    IPAddress,
    PriceType,
    ResourceId,
    Tags,
    ValueObject,
)

__all__ = [
    # Entities
    "Entity",
    "AggregateRoot",
    # Value Objects
    "ValueObject",
    "ResourceId",
    "InstanceId",
    "IPAddress",
    "InstanceType",
    "Tags",
    "ARN",
    "PriceType",
    "AllocationStrategy",
    # Events
    "DomainEvent",
    "EventPublisher",
    "InfrastructureEvent",
    # Repository
    "Repository",
    "AggregateRepository",
    "UnitOfWork",
    "UnitOfWorkFactory",
    # Exceptions
    "DomainException",
    "ValidationError",
    "BusinessRuleViolationError",
    "EntityNotFoundError",
    "ConcurrencyError",
    "InvariantViolationError",
    "InfrastructureError",
    "ConfigurationError",
    # Domain Interfaces (clean)
    "RepositoryProtocol",
    "IRepository",
    "IUnitOfWork",
]
