"""Orchestrator for the dashboard summary aggregate endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    DashboardSummaryInput,
    DashboardSummaryOutput,
    ListRequestsInput,
)
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports.logging_port import LoggingPort
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
_TEMPLATE_PROVIDER_API_KEYS = ["aws", "EC2Fleet", "SpotFleet", "RunInstances", "ASG"]


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

    Recent activity (top-10 table) continues to use the list endpoint with
    limit=10; that is intentional.
    """

    def __init__(
        self,
        list_requests: ListRequestsOrchestrator,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
    ) -> None:
        self._list_requests = list_requests
        self._uow_factory = uow_factory
        self._logger = logger

    async def execute(self, input: DashboardSummaryInput) -> DashboardSummaryOutput:
        self._logger.info("DashboardSummaryOrchestrator: building dashboard aggregate")

        with self._uow_factory.create_unit_of_work() as uow:
            # ---- machines ---------------------------------------------------
            machine_by_status = uow.machines.count_by_status()
            machines_total = sum(machine_by_status.values())
            # Ensure well-known keys are present even when count is 0.
            for key in _MACHINE_STATUS_KEYS:
                machine_by_status.setdefault(key, 0)
            machines_section: dict[str, Any] = {
                "total": machines_total,
                "by_status": machine_by_status,
            }

            # ---- requests (counts) ------------------------------------------
            request_by_status = uow.requests.count_by_status()
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
            provider_api_counts = uow.templates.count_by_provider_api()
            templates_total = sum(provider_api_counts.values())
            for key in _TEMPLATE_PROVIDER_API_KEYS:
                provider_api_counts.setdefault(key, 0)
            templates_section: dict[str, Any] = {
                "total": templates_total,
                "by_provider_api": provider_api_counts,
            }

        # ---- recent activity (top 10 by created_at desc) --------------------
        # Uses the list endpoint with a small limit; this is intentional.
        requests_output = await self._list_requests.execute(
            ListRequestsInput(limit=10, sort="-created_at")
        )
        recent_raw: list[dict[str, Any]] = list(requests_output.requests)

        def _created_at_key(r: dict[str, Any]) -> str:
            # Sort key must be totally ordered — coerce missing values to ""
            # so requests without a created_at land at the end (sort reversed).
            return _to_iso(r.get("created_at")) or ""

        recent_sorted = sorted(recent_raw, key=_created_at_key, reverse=True)[:10]
        recent_activity = [
            {
                "request_id": r.get("request_id", r.get("id", "")),
                "status": r.get("status", ""),
                "request_type": r.get("request_type", r.get("type", "acquire")),
                "template_id": r.get("template_id", ""),
                "created_at": _to_iso(r.get("created_at")),
                # Lifecycle timestamps used by the inline stepper on the
                # dashboard activity table. Pre-formatted as ISO strings; if
                # the source has them as None we forward None and the
                # stepper renders the marker as 'absent / dashed gray'.
                "started_at": _to_iso(r.get("started_at")),
                "first_status_check": _to_iso(r.get("first_status_check")),
                "last_status_check": _to_iso(r.get("last_status_check")),
                "completed_at": _to_iso(r.get("completed_at")),
                "successful_count": int(
                    r.get("successful_count", r.get("fulfilled_count", 0)) or 0
                ),
                "requested_count": int(r.get("requested_count", r.get("count", 0)) or 0),
            }
            for r in recent_sorted
        ]

        return DashboardSummaryOutput(
            machines=machines_section,
            requests=requests_section,
            templates=templates_section,
            recent_activity=recent_activity,
        )
