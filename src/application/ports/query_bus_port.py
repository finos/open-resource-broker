"""Query bus port interface."""

from abc import ABC, abstractmethod
from typing import Any, TypeVar

from application.dto.queries import Query

TQuery = TypeVar("TQuery", bound=Query)
TResult = TypeVar("TResult")


class QueryBusPort(ABC):
    """Port interface for query bus operations.
    
    This port defines the contract for executing queries in the application layer.
    Infrastructure adapters must implement this interface to provide query execution.
    """

    @abstractmethod
    async def execute(self, query: TQuery) -> TResult:
        """Execute a query and return the result.
        
        Args:
            query: The query to execute
            
        Returns:
            The query result
            
        Raises:
            QueryExecutionError: If query execution fails
        """
        ...
