"""Command and Query interfaces for CQRS pattern."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from application.interfaces.command_handler import CommandHandler

T = TypeVar("T")  # Query type
R = TypeVar("R")  # Result type

__all__: list[str] = ["Query", "QueryHandler", "Command", "CommandHandler"]


class Query(ABC):
    """Base interface for queries."""


class QueryHandler(Generic[T, R], ABC):
    """Base interface for query handlers."""

    @abstractmethod
    async def handle(self, query: T) -> R:
        """
        Handle a query asynchronously.

        Args:
            query: Query to handle (strongly typed)

        Returns:
            Query result (strongly typed)

        Raises:
            ValidationError: If query is invalid
        """


class Command(ABC):
    """Base interface for commands."""


# CommandHandler is imported from the centralized location
