"""
Domain Layer - DDD Compliant Bounded Contexts

This domain layer is organized by bounded contexts:
- base/: Shared kernel with base classes and common concepts
- template/: Template bounded context
- machine/: Machine bounded context
- request/: Request bounded context

Each bounded context contains:
- aggregate.py: Aggregate root with business logic
- repository.py: Repository interface for data access
- events.py: Domain events
- value_objects.py: Context-specific value objects
- exceptions.py: Context-specific exceptions
"""

# Export common value objects
# Export base domain primitives
from .base import (
    AggregateRepository,
    AggregateRoot,
    AllocationStrategy,
    DomainEvent,
    DomainException,
    Entity,
    EventPublisher,
    InstanceId,
    InstanceType,
    IPAddress,
    PriceType,
    Repository,
    ResourceId,
    Tags,
    ValueObject,
)
from .machine import Machine, MachineRepository, MachineStatus
from .request import Request, RequestRepository, RequestStatus, RequestType

# Export bounded context aggregates
from .template import Template

__all__: list[str] = [
    # Base primitives
    "Entity",
    "AggregateRoot",
    "ValueObject",
    "DomainEvent",
    "EventPublisher",
    "Repository",
    "AggregateRepository",
    "DomainException",
    # Template context
    "Template",
    "TemplateId",
    "TemplateRepository",
    # Machine context
    "Machine",
    "MachineStatus",
    "MachineRepository",
    # Request context
    "Request",
    "RequestStatus",
    "RequestType",
    "RequestRepository",
    # Common value objects
    "ResourceId",
    "InstanceId",
    "IPAddress",
    "InstanceType",
    "Tags",
    "PriceType",
    "AllocationStrategy",
]
