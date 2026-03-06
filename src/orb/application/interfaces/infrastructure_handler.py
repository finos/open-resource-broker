"""Infrastructure Handler interface for CQRS pattern consistency."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

TRequest = TypeVar("TRequest")
TResponse = TypeVar("TResponse")


class InfrastructureHandler(Generic[TRequest, TResponse], ABC):
    """
    Base interface for infrastructure handlers.

    This interface provides the contract for all infrastructure handlers in the system,
    following the same architectural pattern as other handler interfaces:

    - InfrastructureHandler (interface) -> BaseInfrastructureHandler (implementation)
    - CommandHandler (interface) -> BaseCommandHandler (implementation)
    - QueryHandler (interface) -> BaseQueryHandler (implementation)
    - EventHandler (interface) -> BaseEventHandler (implementation)

    This ensures architectural consistency across all handler types in the CQRS system.
    """

    @abstractmethod
    async def handle(self, request: TRequest) -> TResponse:
        """
        Handle an infrastructure request.

        Args:
            request: Infrastructure request to handle (strongly typed)

        Returns:
            Infrastructure response (strongly typed)

        Raises:
            ValidationError: If request is invalid
            InfrastructureError: If infrastructure operation fails
        """
