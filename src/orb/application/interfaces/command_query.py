"""Command and Query interfaces for CQRS pattern."""

from typing import Generic, TypeVar

from orb.application.dto.base import Command, Query
from orb.application.interfaces.command_handler import CommandHandler

T = TypeVar("T")  # Query type
R = TypeVar("R")  # Result type

__all__: list[str] = ["Command", "CommandHandler", "Query", "QueryHandler"]


class QueryHandler(Generic[T, R]):
    """Base interface for query handlers."""

    async def handle(self, query: T) -> R:  # type: ignore[return]
        pass
