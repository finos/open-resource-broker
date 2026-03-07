"""Provider Handler interface for CQRS pattern consistency."""

from abc import ABC, abstractmethod
from typing import Generic, Optional, TypeVar

TRequest = TypeVar("TRequest")
TResponse = TypeVar("TResponse")


class ProviderHandler(Generic[TRequest, TResponse], ABC):
    """
    Base interface for provider handlers.

    This interface provides the contract for all provider handlers in the system,
    following the same architectural pattern as other handler interfaces:

    - ProviderHandler (interface) → BaseProviderHandler (implementation)
    - CommandHandler (interface) → BaseCommandHandler (implementation)
    - QueryHandler (interface) → BaseQueryHandler (implementation)
    - EventHandler (interface) → BaseEventHandler (implementation)
    - InfrastructureHandler (interface) → BaseInfrastructureHandler (implementation)

    This ensures architectural consistency across all handler types in the CQRS system.
    """

    @abstractmethod
    async def handle(self, request: TRequest, context: Optional[object] = None) -> TResponse:
        """
        Handle a provider request.

        Args:
            request: Provider request to handle (strongly typed)
            context: Optional provider context

        Returns:
            Provider response (strongly typed)

        Raises:
            ValidationError: If request is invalid
            ProviderError: If provider operation fails
            InfrastructureError: If infrastructure operation fails
        """
