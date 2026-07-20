"""System-level API routes (dashboard summary, etc.)."""

from __future__ import annotations

import dataclasses
from typing import Any

try:
    from fastapi import APIRouter, Depends
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import get_dashboard_summary_orchestrator, require_role
from orb.application.services.orchestration.dtos import DashboardSummaryInput
from orb.infrastructure.error.decorators import handle_rest_exceptions

router = APIRouter(prefix="/system", tags=["System"])

DASHBOARD_ORCHESTRATOR = Depends(get_dashboard_summary_orchestrator)


def _serialisable(obj: Any) -> Any:
    """Recursively convert dataclasses / non-JSON-safe values to plain types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _serialisable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: _serialisable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialisable(item) for item in obj]
    return obj


@router.get(
    "/dashboard",
    operation_id="getDashboardSummary",
    summary="Dashboard Summary",
    description="Aggregate counts for machines, requests and templates for the UI dashboard.",
)
@handle_rest_exceptions(endpoint="/api/v1/system/dashboard", method="GET")
async def get_dashboard_summary(
    orchestrator=DASHBOARD_ORCHESTRATOR,
    _user=Depends(require_role("viewer")),
) -> JSONResponse:
    """Return aggregated dashboard data without client-side reduction."""
    output = await orchestrator.execute(DashboardSummaryInput())
    return JSONResponse(content=_serialisable(output), status_code=200)
