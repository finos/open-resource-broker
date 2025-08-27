"""Command handler interface for CQRS pattern."""

import asyncio
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from application.dto.base import BaseCommand, BaseResponse

TCommand = TypeVar("TCommand", bound=BaseCommand)
TResponse = TypeVar("TResponse", bound=BaseResponse)


class CommandHandler(Generic[TCommand, TResponse], ABC):
    """
    Command handler interface for CQRS pattern.

    This is the single source of truth for all command handlers in the system.
    Supports both synchronous and asynchronous command handling patterns.

    Command handlers are responsible for:
    - Validating commands
    - Executing business logic
    - Persisting changes
    - Publishing domain events
    - Returning appropriate responses
    """

    @abstractmethod
    async def handle(self, command: TCommand) -> TResponse:
        """
        Handle a command asynchronously and return response.

        This is the primary method that all command handlers must implement.
        Async-first design enables better scalability and resource utilization.

        Args:
            command: Command to handle (strongly typed)

        Returns:
            Command response (strongly typed)

        Raises:
            ValidationError: If command is invalid
            BusinessRuleViolationError: If command violates business rules
            InfrastructureError: If infrastructure operation fails
        """

    def handle_sync(self, command: TCommand) -> TResponse:
        """
        Provide synchronous wrapper for backward compatibility.

        This method provides a synchronous interface to the async handle method
        for cases where async/await cannot be used (e.g., synchronous tests).

        Args:
            command: Command to handle (strongly typed)

        Returns:
            Command response (strongly typed)
        """
        return asyncio.run(self.handle(command))

    def can_handle(self, command: BaseCommand) -> bool:
        """
        Check if this handler can handle the given command.

        Default implementation checks if the command is an instance of the
        expected command type. Override for custom logic.

        Args:
            command: Command to check

        Returns:
            True if this handler can handle the command, False otherwise
        """
        # Get the command type from the generic type annotation
        try:
            command_type = self.__class__.__orig_bases__[0].__args__[0]
            return isinstance(command, command_type)
        except (AttributeError, IndexError):
            # Fallback for cases where generic type info is not available
            return True
