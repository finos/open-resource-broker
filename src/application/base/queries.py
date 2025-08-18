"""Base query handlers - foundation for query processing."""

from typing import Protocol

from application.dto.base import BaseQuery, BaseResponse


class QueryBus(Protocol):
    """Protocol for query bus."""

    async def execute(self, query: BaseQuery) -> BaseResponse:
        """Execute a query asynchronously for processing."""
        ...

    def register_handler(self, query_type: type, handler) -> None:
        """Register a query handler."""
        ...
