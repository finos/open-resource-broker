"""Orchestrator for the dashboard summary aggregate endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from orb.application.ports.exceptions import RepositoryQueryError
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    DashboardSummaryInput,
    DashboardSummaryOutput,
)
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort
from orb.domain.request.request_types import RequestStatus

# RequestStatus enum values are: pending / in_progress / acquiring /
# complete / failed / cancelled / timeout / partial. Note "complete"
# (singular) — NOT "completed".
_TERMINAL_STATUSES = frozenset(s.value for s in RequestStatus if s.is_terminal())

_MACHINE_STATUS_KEYS = ["running", "pending", "stopped", "terminated", "shutting-down"]
_REQUEST_STATUS_KEYS = [
    "pending",
    "in_progress",
    "acquiring",
    "complete",
    "failed",
    "partial",
    "cancelled",
    "timeout",
]


def _to_iso(value: Any) -> Optional[str]:
    """Coerce a datetime-like value to an ISO-8601 string.

    Returns None when the input is None so the UI's inline stepper can
    distinguish absent lifecycle timestamps (rendered as a dashed-gray
    marker) from a literal empty string.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, str):
        return value
    return str(value)


class DashboardSummaryOrchestrator(OrchestratorBase[DashboardSummaryInput, DashboardSummaryOutput]):
    """Aggregate orchestrator that builds the dashboard summary in Python.

    Per-status and per-provider-api counts are sourced from dedicated
    repository GROUP BY queries (``count_by_status`` / ``count_by_provider_api``)
    instead of listing all rows with limit=100_000 against handlers that
    clamp at 1000.  This makes the stat cards accurate at any data scale.

    Recent activity (top-10 table) is fetched via
    ``uow.requests.list_recent_activity(10)`` inside the same UoW-scoped
    ``with`` block as the count queries.  This logical grouping minimises
    wall-clock drift between the count and activity figures.

    **Consistency caveat**: under the SQL backend
    ``SQLStorageStrategy.begin_transaction`` is a no-op (it logs but does not
    open a database-level transaction or acquire a snapshot).  The UoW
    therefore does *not* provide repeatable-read isolation: each query runs in
    its own implicit transaction, so concurrent writes between queries can
    cause minor discrepancies (e.g. a request that transitions between the
    count query and the activity query).  Consistency is best-effort for the
    SQL backend.  Backends that implement true snapshot isolation (e.g. a
    future PostgreSQL adapter using ``BEGIN ISOLATION LEVEL REPEATABLE READ``)
    would provide stronger guarantees without any change to this orchestrator.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        provider_registry: ProviderRegistryPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._logger = logger
        self._provider_registry = provider_registry

    async def execute(self, input: DashboardSummaryInput) -> DashboardSummaryOutput:
        self._logger.info("DashboardSummaryOrchestrator: building dashboard aggregate")

        with self._uow_factory.create_unit_of_work() as uow:
            # ---- machines ---------------------------------------------------
            try:
                machine_by_status = uow.machines.count_by_status()
            except RepositoryQueryError as exc:
                self._logger.warning(
                    "DashboardSummaryOrchestrator: count_by_status failed for machines: %s; "
                    "returning empty counts",
                    exc,
                )
                machine_by_status = {}
            machines_total = sum(machine_by_status.values())
            # Ensure well-known keys are present even when count is 0.
            for key in _MACHINE_STATUS_KEYS:
                machine_by_status.setdefault(key, 0)
            machines_section: dict[str, Any] = {
                "total": machines_total,
                "by_status": machine_by_status,
            }

            # ---- requests (counts) ------------------------------------------
            try:
                request_by_status = uow.requests.count_by_status()
            except RepositoryQueryError as exc:
                self._logger.warning(
                    "DashboardSummaryOrchestrator: count_by_status failed for requests: %s; "
                    "returning empty counts",
                    exc,
                )
                request_by_status = {}
            requests_total = sum(request_by_status.values())
            in_flight = sum(
                count
                for status_val, count in request_by_status.items()
                if status_val not in _TERMINAL_STATUSES
            )
            for key in _REQUEST_STATUS_KEYS:
                request_by_status.setdefault(key, 0)
            requests_section: dict[str, Any] = {
                "total": requests_total,
                "in_flight": in_flight,
                "by_status": request_by_status,
            }

            # ---- templates (counts) -----------------------------------------
            try:
                provider_api_counts = uow.templates.count_by_provider_api()
            except RepositoryQueryError as exc:
                self._logger.warning(
                    "DashboardSummaryOrchestrator: count_by_provider_api failed for templates: %s; "
                    "returning empty counts",
                    exc,
                )
                provider_api_counts = {}
            templates_total = sum(provider_api_counts.values())
            try:
                _provider_api_keys = self._provider_registry.list_all_provider_apis()
            except Exception:
                _provider_api_keys = []
            for key in _provider_api_keys:
                provider_api_counts.setdefault(key, 0)
            templates_section: dict[str, Any] = {
                "total": templates_total,
                "by_provider_api": provider_api_counts,
            }

            # ---- recent activity (top 10 by created_at desc) ----------------
            # Fetched within the same UoW block as the count queries to minimise
            # wall-clock drift; see class docstring for consistency caveats.
            recent_requests = uow.requests.list_recent_activity(10)

        recent_activity = [
            {
                "request_id": str(getattr(r.request_id, "value", r.request_id)),
                "status": str(getattr(r.status, "value", r.status)),
                "request_type": str(getattr(r.request_type, "value", r.request_type)),
                "template_id": r.template_id or "",
                "created_at": _to_iso(r.created_at),
                # Lifecycle timestamps used by the inline stepper on the
                # dashboard activity table. Pre-formatted as ISO strings; if
                # the source has them as None we forward None and the
                # stepper renders the marker as 'absent / dashed gray'.
                "started_at": _to_iso(r.started_at),
                "first_status_check": _to_iso(r.first_status_check),
                "last_status_check": _to_iso(r.last_status_check),
                "completed_at": _to_iso(r.completed_at),
                "successful_count": int(r.successful_count or 0),
                "requested_count": int(r.requested_count or 0),
            }
            for r in recent_requests
        ]

        return DashboardSummaryOutput(
            machines=machines_section,
            requests=requests_section,
            templates=templates_section,
            recent_activity=recent_activity,
        )
