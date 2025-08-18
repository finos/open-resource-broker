"""Event Handler interface for CQRS pattern consistency."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from domain.base.events import DomainEvent

TEvent = TypeVar("TEvent", bound=DomainEvent)


class EventHandler(Generic[TEvent], ABC):
    """
    Base interface for event handlers.

    This interface provides the contract for all event handlers in the system,
    following the same architectural pattern as CommandHandler and QueryHandler:

    - EventHandler (interface) -> BaseEventHandler (implementation)
    - CommandHandler (interface) -> BaseCommandHandler (implementation)
    - QueryHandler (interface) -> BaseQueryHandler (implementation)

    This ensures architectural consistency across all handler types in the CQRS system.
    """

    @abstractmethod
    async def handle(self, event: TEvent) -> None:
        """
        Handle a domain event.

        Args:
            event: Domain event to handle (strongly typed)

        Raises:
            ValidationError: If event is invalid
            BusinessRuleViolationError: If event violates business rules
            InfrastructureError: If infrastructure operation fails
        """
