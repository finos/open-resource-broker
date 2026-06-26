"""Orchestrator for the dashboard summary aggregate endpoint."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any, Optional

from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    DashboardSummaryInput,
    DashboardSummaryOutput,
    ListMachinesInput,
    ListRequestsInput,
    ListTemplatesInput,
)
from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
from orb.domain.base.ports.logging_port import LoggingPort

# RequestStatus enum values are: pending / in_progress / acquiring /
# complete / failed / cancelled / timeout / partial. Note "complete"
# (singular) — NOT "completed". The dashboard previously used the wrong
# key "completed" which never matched, leaving every fulfilled request
# counted as in_flight and the Completed stat card stuck at 0.
_TERMINAL_STATUSES = frozenset({"complete", "failed", "cancelled", "timeout", "partial"})

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


class DashboardSummaryOrchestrator(
    OrchestratorBase[DashboardSummaryInput, DashboardSummaryOutput]
):
    """Aggregate orchestrator that builds the dashboard summary in Python.

    Pulls all machines, requests and templates from the existing list
    orchestrators and rolls up counts server-side so the UI does not have
    to reduce thousands of records client-side.
    """

    def __init__(
        self,
        list_machines: ListMachinesOrchestrator,
        list_requests: ListRequestsOrchestrator,
        list_templates: ListTemplatesOrchestrator,
        logger: LoggingPort,
    ) -> None:
        self._list_machines = list_machines
        self._list_requests = list_requests
        self._list_templates = list_templates
        self._logger = logger

    async def execute(self, input: DashboardSummaryInput) -> DashboardSummaryOutput:
        self._logger.info("DashboardSummaryOrchestrator: building dashboard aggregate")

        # ---- machines -------------------------------------------------------
        machines_output = await self._list_machines.execute(
            ListMachinesInput(limit=100_000)
        )
        machine_by_status: dict[str, int] = {k: 0 for k in _MACHINE_STATUS_KEYS}
        for m in machines_output.machines:
            raw = getattr(m, "status", None) or (
                m.get("status") if isinstance(m, dict) else None
            )
            status_key = str(raw).lower() if raw else "unknown"
            if status_key in machine_by_status:
                machine_by_status[status_key] += 1
            else:
                machine_by_status[status_key] = machine_by_status.get(status_key, 0) + 1

        # total_count is the post-filter row count from the handler before
        # pagination/clamp; count is len(items) which the handler clamps to
        # 1000. Use total_count so the dashboard headline number is honest
        # at any scale. by_status remains computed over the slice the
        # handler returned (capped); a proper count_by_status aggregate
        # is tracked for a follow-up.
        machines_total = (
            machines_output.total_count
            if machines_output.total_count is not None
            else machines_output.count
        )
        machines_section: dict[str, Any] = {
            "total": machines_total,
            "by_status": machine_by_status,
        }

        # ---- requests -------------------------------------------------------
        requests_output = await self._list_requests.execute(
            ListRequestsInput(limit=100_000)
        )
        request_by_status: dict[str, int] = {k: 0 for k in _REQUEST_STATUS_KEYS}
        in_flight = 0
        recent_raw: list[dict[str, Any]] = []

        for r in requests_output.requests:
            raw_status = str(r.get("status", "")).lower()
            if raw_status in request_by_status:
                request_by_status[raw_status] += 1
            else:
                request_by_status[raw_status] = request_by_status.get(raw_status, 0) + 1
            if raw_status not in _TERMINAL_STATUSES:
                in_flight += 1
            recent_raw.append(r)

        requests_total = (
            requests_output.total_count
            if requests_output.total_count is not None
            else requests_output.count
        )
        requests_section: dict[str, Any] = {
            "total": requests_total,
            "in_flight": in_flight,
            "by_status": request_by_status,
        }

        # ---- recent activity (top 10 by created_at desc) --------------------
        def _created_at_key(r: dict[str, Any]) -> str:
            val = r.get("created_at")
            return _to_iso(val)

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

        # ---- templates ------------------------------------------------------
        templates_output = await self._list_templates.execute(
            ListTemplatesInput(active_only=False, limit=100_000)
        )
        provider_api_counts: dict[str, int] = {k: 0 for k in _TEMPLATE_PROVIDER_API_KEYS}
        for t in templates_output.templates:
            if isinstance(t, dict):
                api = str(t.get("provider_api", "")).strip()
            else:
                api = str(getattr(t, "provider_api", "")).strip()
            if api in provider_api_counts:
                provider_api_counts[api] += 1
            else:
                provider_api_counts[api] = provider_api_counts.get(api, 0) + 1

        templates_total = (
            templates_output.total_count
            if templates_output.total_count is not None
            else templates_output.count
        )
        templates_section: dict[str, Any] = {
            "total": templates_total,
            "by_provider_api": provider_api_counts,
        }

        return DashboardSummaryOutput(
            machines=machines_section,
            requests=requests_section,
            templates=templates_section,
            recent_activity=recent_activity,
        )
