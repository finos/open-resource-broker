"""Query handlers that return per-status (or per-provider-api) row counts.

These handlers exist to avoid the dashboard listing thousands of entities
into Python just to aggregate them.  Each handler issues a single GROUP BY
query through the repository layer, which delegates to a SQL
``SELECT col, COUNT(*) GROUP BY col`` when the storage strategy supports it,
or falls back to a list-and-group slow path for file-based backends.
"""

from __future__ import annotations

from orb.application.base.handlers import BaseQueryHandler
from orb.application.decorators import query_handler
from orb.application.dto.queries import (
    CountMachinesByStatusQuery,
    CountRequestsByStatusQuery,
    CountTemplatesByProviderApiQuery,
)
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports import ErrorHandlingPort, LoggingPort


@query_handler(CountMachinesByStatusQuery)
class CountMachinesByStatusHandler(BaseQueryHandler[CountMachinesByStatusQuery, dict[str, int]]):
    """Return ``{status: count}`` for all machine rows."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: CountMachinesByStatusQuery) -> dict[str, int]:
        """Execute count-machines-by-status query."""
        self.logger.debug("CountMachinesByStatusHandler: executing GROUP BY status")
        with self.uow_factory.create_unit_of_work() as uow:
            return uow.machines.count_by_status()


@query_handler(CountRequestsByStatusQuery)
class CountRequestsByStatusHandler(BaseQueryHandler[CountRequestsByStatusQuery, dict[str, int]]):
    """Return ``{status: count}`` for all request rows."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: CountRequestsByStatusQuery) -> dict[str, int]:
        """Execute count-requests-by-status query."""
        self.logger.debug("CountRequestsByStatusHandler: executing GROUP BY status")
        with self.uow_factory.create_unit_of_work() as uow:
            return uow.requests.count_by_status()


@query_handler(CountTemplatesByProviderApiQuery)
class CountTemplatesByProviderApiHandler(
    BaseQueryHandler[CountTemplatesByProviderApiQuery, dict[str, int]]
):
    """Return ``{provider_api: count}`` for all template rows."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: CountTemplatesByProviderApiQuery) -> dict[str, int]:
        """Execute count-templates-by-provider-api query."""
        self.logger.debug("CountTemplatesByProviderApiHandler: executing GROUP BY provider_api")
        with self.uow_factory.create_unit_of_work() as uow:
            return uow.templates.count_by_provider_api()
