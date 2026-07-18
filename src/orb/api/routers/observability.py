"""Observability API routes — machine metrics, request timeline, and telemetry status."""

from typing import Any, Optional

try:
    from fastapi import APIRouter, Depends, Query
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import (
    get_machine_orchestrator,
    get_request_status_orchestrator,
    require_role,
)
from orb.application.services.orchestration.dtos import (
    GetMachineInput,
    GetRequestStatusInput,
)
from orb.infrastructure.error.decorators import handle_rest_exceptions

router = APIRouter(prefix="", tags=["Observability"])

# Module-level dependency variables to avoid B008 warnings
GET_MACHINE_ORCHESTRATOR = Depends(get_machine_orchestrator)
GET_REQUEST_STATUS_ORCHESTRATOR = Depends(get_request_status_orchestrator)

_VALID_RANGES = {"1h", "6h", "24h", "7d"}

_FAILURE_STATUSES = {
    "failed",
    "error",
    "cancelled",
    "canceled",
    "partial",
    "partial_failure",
    "partial_success",
}


@router.get(
    "/machines/{machine_id}/metrics",
    operation_id="getMachineMetrics",
    summary="Get Machine Metrics",
    description=(
        "Return time-series metrics for a machine. "
        "Currently returns a stub structure with empty point arrays. "
        "Real data will be sourced from CloudWatch once wired up."
    ),
)
@handle_rest_exceptions(endpoint="/api/v1/machines/{machine_id}/metrics", method="GET")
async def get_machine_metrics(
    machine_id: str,
    range: str = Query("1h", description="Time range — one of: 1h, 6h, 24h, 7d"),
    orchestrator=GET_MACHINE_ORCHESTRATOR,
    _user=Depends(require_role("viewer")),
) -> JSONResponse:
    """
    Return time-series metrics for a specific machine.

    The ``range`` parameter controls the window of data requested.
    Valid values: ``1h``, ``6h``, ``24h``, ``7d``.

    **Current implementation:** stub only — all ``points`` arrays are empty and
    ``source`` is ``"stub"``.  Consumers should check ``source`` before charting.

    Note:
        The stub shape is intentional and stable; the ``source`` field will change
        from ``"stub"`` to ``"cloudwatch"`` once real metrics are wired up.
    """
    # Normalise range; default to 1h for unrecognised values.
    if range not in _VALID_RANGES:
        range = "1h"

    result = await orchestrator.execute(GetMachineInput(machine_id=machine_id))
    if result.machine is None:
        return JSONResponse(
            content={"detail": f"Machine {machine_id} not found"},
            status_code=404,
        )

    # Future: replace empty-points stub with CloudWatch GetMetricStatistics calls.
    # Approach: derive the time window from `range`, query each metric series
    # (AWS/EC2 for cpu; custom namespace for memory/network), and map
    # CW datapoints → {"ts": <ISO>, "value": <float>} sorted ascending by ts.
    response: dict[str, Any] = {
        "machine_id": machine_id,
        "range": range,
        "series": [
            {"name": "cpu_percent", "unit": "%", "points": []},
            {"name": "memory_percent", "unit": "%", "points": []},
            {"name": "network_in_bytes", "unit": "bytes", "points": []},
            {"name": "network_out_bytes", "unit": "bytes", "points": []},
        ],
        "source": "stub",
    }
    return JSONResponse(content=response)


@router.get(
    "/requests/{request_id}/timeline",
    operation_id="getRequestTimeline",
    summary="Get Request Timeline",
    description=(
        "Return the lifecycle event log for a request, synthesised from the "
        "status-transition timestamps stored on the Request aggregate."
    ),
)
@handle_rest_exceptions(endpoint="/api/v1/requests/{request_id}/timeline", method="GET")
async def get_request_timeline(
    request_id: str,
    orchestrator=GET_REQUEST_STATUS_ORCHESTRATOR,
    _user=Depends(require_role("viewer")),
) -> JSONResponse:
    """
    Return a chronological list of lifecycle events for a request.

    Events are synthesised from the timestamp fields recorded on the Request
    aggregate (``created_at``, ``started_at``, ``first_status_check``,
    ``last_status_check``, ``completed_at``).  Entries whose timestamp is
    ``None`` are omitted.  A terminal ``failed`` or ``partial`` event is
    appended when the final status indicates failure.
    """
    result = await orchestrator.execute(
        GetRequestStatusInput(request_ids=[request_id], verbose=True)
    )

    requests_list = result.requests
    if not requests_list:
        return JSONResponse(
            content={"detail": f"Request {request_id} not found"},
            status_code=404,
        )

    req = requests_list[0]

    # Guard: orchestrator may return an error dict when the ID is unknown.
    if "error" in req and len(req) <= 2:
        return JSONResponse(
            content={"detail": f"Request {request_id} not found"},
            status_code=404,
        )

    def _ts(value: Optional[Any]) -> Optional[str]:
        """Normalise a timestamp value to an ISO-8601 string, or None."""
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        s = str(value)
        return s if s else None

    events: list[dict[str, Any]] = []

    # --- Core lifecycle events, in logical order ---
    _candidates: list[tuple[Optional[str], str, str]] = [
        (_ts(req.get("created_at")), "created", "Request created"),
        (_ts(req.get("started_at")), "started", "Provisioning started"),
        (
            _ts(req.get("first_status_check")),
            "first_status_check",
            "First provider status check",
        ),
        (
            _ts(req.get("last_status_check")),
            "last_status_check",
            "Last provider status check",
        ),
        (_ts(req.get("completed_at")), "completed", "Request completed"),
    ]

    for ts, event_type, message in _candidates:
        if ts is not None:
            events.append({"ts": ts, "type": event_type, "message": message})

    # --- Terminal failure / partial event ---
    status: str = str(req.get("status", "")).lower()
    status_reason: Optional[str] = req.get("message") or None

    if status in _FAILURE_STATUSES:
        # Anchor the failure event to the last known timestamp.
        failure_ts = (
            _ts(req.get("last_status_check"))
            or _ts(req.get("completed_at"))
            or _ts(req.get("created_at"))
        )
        if failure_ts is not None:
            failure_type = "partial" if "partial" in status else "failed"
            failure_message = (
                status_reason
                if status_reason
                else ("Partial completion" if failure_type == "partial" else "Request failed")
            )
            events.append({"ts": failure_ts, "type": failure_type, "message": failure_message})

    # Sort by timestamp ascending (ISO-8601 strings sort lexicographically).
    events.sort(key=lambda e: e["ts"])

    return JSONResponse(content={"request_id": request_id, "events": events})


@router.get(
    "/observability/telemetry",
    operation_id="getTelemetryStatus",
    summary="Get Telemetry Status",
    description=(
        "Return the current OpenTelemetry instrumentation status — whether telemetry "
        "is enabled, which metric and trace exporters are active, the service name, "
        "and which auto-instrumentors have been wired.  Read-only; no auth-sensitive "
        "configuration detail is included."
    ),
)
async def get_telemetry_status(
    _user=Depends(require_role("viewer")),
) -> JSONResponse:
    """
    Return OpenTelemetry instrumentation status.

    The response shape is::

        {
          "enabled": true,
          "service_name": "orb",
          "metrics_exporters": ["prometheus"],
          "traces_exporter": null,
          "traces_sample_rate": 0.1,
          "instrumentors": {
            "fastapi": true,
            "sqlalchemy": true,
            "botocore": true,
            "click": true,
            "system_metrics": true,
            "logging": true
          }
        }

    When telemetry is disabled or the SDK is not installed the response is::

        {"enabled": false}
    """
    try:
        from orb.config.schemas.app_schema import AppConfig
        from orb.domain.base.ports.configuration_port import ConfigurationPort
        from orb.infrastructure.di.container import get_container

        config_port = get_container().get(ConfigurationPort)
        app_config: AppConfig = config_port.get_typed(AppConfig)  # type: ignore[arg-type]
        otel_cfg = app_config.observability
    except Exception:
        return JSONResponse(content={"enabled": False})

    if not otel_cfg.enabled:
        return JSONResponse(content={"enabled": False})

    payload: dict[str, Any] = {
        "enabled": True,
        "service_name": otel_cfg.service_name,
        "metrics_exporters": otel_cfg.metrics_exporters,
        "traces_exporter": otel_cfg.traces_exporter,
        "traces_sample_rate": otel_cfg.traces_sample_rate,
        "instrumentors": {
            "fastapi": otel_cfg.instrument_fastapi,
            "sqlalchemy": otel_cfg.instrument_sqlalchemy,
            "botocore": otel_cfg.instrument_botocore,
            "click": otel_cfg.instrument_click,
            "system_metrics": otel_cfg.instrument_system_metrics,
            "logging": otel_cfg.instrument_logging,
        },
    }
    return JSONResponse(content=payload)
