"""Cleanup query handlers for CQRS compliance."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# Define query classes inline since they're not in dto/queries.py yet
from pydantic import BaseModel, ConfigDict

from orb.application.base.handlers import BaseQueryHandler
from orb.application.decorators import query_handler
from orb.application.interfaces.command_query import Query
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports import ErrorHandlingPort, LoggingPort


class ListCleanableRequestsQuery(Query, BaseModel):
    """Query to list requests eligible for cleanup."""

    model_config = ConfigDict(frozen=True)

    older_than_days: int


class ListCleanableResourcesQuery(Query, BaseModel):
    """Query to list resources eligible for cleanup."""

    model_config = ConfigDict(frozen=True)


@query_handler(ListCleanableRequestsQuery)
class ListCleanableRequestsHandler(BaseQueryHandler[ListCleanableRequestsQuery, dict[str, Any]]):
    """Handler for listing requests eligible for cleanup."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize the instance."""
        super().__init__(logger, error_handler)
        self._uow_factory = uow_factory

    async def execute_query(self, query: ListCleanableRequestsQuery) -> dict[str, Any]:
        """Execute list cleanable requests query."""
        self.logger.info(
            "Listing requests eligible for cleanup (older than %d days)", query.older_than_days
        )

        cutoff_date = datetime.utcnow() - timedelta(days=query.older_than_days)

        try:
            with self._uow_factory.create_unit_of_work() as uow:
                # Get all requests
                all_requests = uow.requests.list_all()

                # Filter requests older than cutoff date
                cleanable_requests = []
                for request in all_requests:
                    if hasattr(request, "created_at") and request.created_at:
                        if request.created_at < cutoff_date:
                            cleanable_requests.append(
                                {
                                    "request_id": str(request.request_id),
                                    "status": str(request.status),
                                    "created_at": request.created_at.isoformat(),
                                    "age_days": (datetime.utcnow() - request.created_at).days,
                                }
                            )

                return {
                    "status": "success",
                    "cleanable_requests": cleanable_requests,
                    "total_count": len(cleanable_requests),
                    "cutoff_date": cutoff_date.isoformat(),
                    "older_than_days": query.older_than_days,
                }

        except Exception as e:
            self.logger.error("Failed to list cleanable requests: %s", e, exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "cleanable_requests": [],
                "total_count": 0,
            }


@query_handler(ListCleanableResourcesQuery)
class ListCleanableResourcesHandler(BaseQueryHandler[ListCleanableResourcesQuery, dict[str, Any]]):
    """Handler for listing resources eligible for cleanup."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize the instance."""
        super().__init__(logger, error_handler)
        self._uow_factory = uow_factory

    async def execute_query(self, query: ListCleanableResourcesQuery) -> dict[str, Any]:
        """Execute list cleanable resources query."""
        self.logger.info("Listing resources eligible for cleanup")

        try:
            with self._uow_factory.create_unit_of_work() as uow:
                # Get all machines
                all_machines = uow.machines.list_all()

                # Get all requests
                all_requests = uow.requests.list_all()

                # Identify orphaned machines (no associated request)
                request_ids = {str(r.request_id) for r in all_requests}
                orphaned_machines = []

                for machine in all_machines:
                    if hasattr(machine, "request_id") and machine.request_id:
                        if str(machine.request_id) not in request_ids:
                            orphaned_machines.append(
                                {
                                    "machine_id": str(machine.machine_id),
                                    "request_id": str(machine.request_id),
                                    "status": str(machine.status),
                                }
                            )

                return {
                    "status": "success",
                    "orphaned_machines": orphaned_machines,
                    "total_machines": len(all_machines),
                    "total_requests": len(all_requests),
                    "orphaned_count": len(orphaned_machines),
                }

        except Exception as e:
            self.logger.error("Failed to list cleanable resources: %s", e, exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "orphaned_machines": [],
                "orphaned_count": 0,
            }
