"""Specialized query handlers for application services."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from orb.application.base.handlers import BaseQueryHandler
from orb.application.decorators import query_handler
from orb.application.dto.queries import (
    GetActiveMachineCountQuery,
    GetRequestSummaryQuery,
)
from orb.application.dto.responses import RequestSummaryDTO
from orb.application.request.queries import GetRequestMetricsQuery
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports import ErrorHandlingPort, LoggingPort
from orb.domain.machine.value_objects import MachineStatus

_ACTIVE_MACHINE_STATUSES = frozenset(
    s.value for s in (MachineStatus.RUNNING, MachineStatus.PENDING, MachineStatus.LAUNCHING)
)


@query_handler(GetActiveMachineCountQuery)
class GetActiveMachineCountHandler(BaseQueryHandler[GetActiveMachineCountQuery, int]):
    """Return active machine count using count_by_status GROUP BY aggregation."""

    def __init__(self, uow_factory: UnitOfWorkFactory, logger: LoggingPort, error_handler: ErrorHandlingPort) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: GetActiveMachineCountQuery) -> int:
        self.logger.debug("GetActiveMachineCountHandler: count_by_status aggregation")
        with self.uow_factory.create_unit_of_work() as uow:
            by_status = uow.machines.count_by_status()
            count = sum(v for k, v in by_status.items() if k in _ACTIVE_MACHINE_STATUSES)
        self.logger.info("Active machine count: %s", count)
        return count


@query_handler(GetRequestSummaryQuery)
class GetRequestSummaryHandler(BaseQueryHandler[GetRequestSummaryQuery, RequestSummaryDTO]):
    """Return per-request machine breakdown grouped by all observed statuses."""

    def __init__(self, uow_factory: UnitOfWorkFactory, logger: LoggingPort, error_handler: ErrorHandlingPort) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: GetRequestSummaryQuery) -> RequestSummaryDTO:
        self.logger.debug("GetRequestSummaryHandler: request_id=%s", query.request_id)
        with self.uow_factory.create_unit_of_work() as uow:
            request = uow.requests.get_by_id(query.request_id)
            if not request:
                raise EntityNotFoundError("Request", query.request_id)
            machines = uow.machines.find_by_request_id(query.request_id)
            machine_statuses: dict[str, int] = {}
            for m in machines:
                key = str(getattr(m.status, "value", m.status))
                machine_statuses[key] = machine_statuses.get(key, 0) + 1
            summary = RequestSummaryDTO(
                request_id=str(request.request_id),
                status=request.status.value,
                total_machines=len(machines),
                machine_statuses=machine_statuses,
                created_at=request.created_at,
            )
        self.logger.info("GetRequestSummaryHandler: request_id=%s total_machines=%d", query.request_id, len(machines))
        return summary


@query_handler(GetRequestMetricsQuery)
class GetRequestMetricsHandler(BaseQueryHandler[GetRequestMetricsQuery, dict[str, Any]]):
    """Return time-windowed request metrics via get_metrics_by_date_range."""

    def __init__(self, uow_factory: UnitOfWorkFactory, logger: LoggingPort, error_handler: ErrorHandlingPort) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: GetRequestMetricsQuery) -> dict[str, Any]:
        self.logger.debug("GetRequestMetricsHandler: start=%s end=%s group_by=%s", query.start_date, query.end_date, query.group_by)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        start_dt: datetime = epoch
        end_dt: datetime = now
        if query.start_date:
            try:
                parsed = datetime.fromisoformat(query.start_date)
                start_dt = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                self.logger.warning("GetRequestMetricsHandler: invalid start_date %r; using epoch", query.start_date)
        if query.end_date:
            try:
                parsed = datetime.fromisoformat(query.end_date)
                end_dt = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                self.logger.warning("GetRequestMetricsHandler: invalid end_date %r; using now", query.end_date)
        with self.uow_factory.create_unit_of_work() as uow:
            metrics = uow.requests.get_metrics_by_date_range(start_dt, end_dt)
        return {"start_date": start_dt.isoformat(), "end_date": end_dt.isoformat(), "group_by": query.group_by, "metrics": metrics}
