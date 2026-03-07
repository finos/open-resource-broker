"""Base query service for UoW pattern abstraction."""

from abc import ABC
from typing import Any, Callable

from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports.logging_port import LoggingPort


class BaseQueryService(ABC):
    """Base service for query operations with UoW abstraction."""

    def __init__(self, uow_factory: UnitOfWorkFactory, logger: LoggingPort):
        self.uow_factory = uow_factory
        self.logger = logger

    async def execute_with_uow(self, operation: Callable) -> Any:
        """Execute operation within UoW context."""
        with self.uow_factory.create_unit_of_work() as uow:
            return await operation(uow)
