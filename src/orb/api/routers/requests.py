"""Request management API routes."""

import asyncio
import json
from typing import Optional

try:
    from fastapi import APIRouter, Depends, Query
    from fastapi.responses import JSONResponse, StreamingResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import (
    get_cancel_request_orchestrator,
    get_list_requests_orchestrator,
    get_list_return_requests_orchestrator,
    get_request_status_orchestrator,
    get_scheduler_strategy,
)
from orb.application.services.orchestration.dtos import (
    CancelRequestInput,
    GetRequestStatusInput,
    ListRequestsInput,
    ListReturnRequestsInput,
)
from orb.infrastructure.error.decorators import handle_rest_exceptions

router = APIRouter(prefix="/requests", tags=["Requests"])

# Module-level dependency variables to avoid B008 warnings
STATUS_ORCHESTRATOR = Depends(get_request_status_orchestrator)
LIST_ORCHESTRATOR = Depends(get_list_requests_orchestrator)
RETURN_LIST_ORCHESTRATOR = Depends(get_list_return_requests_orchestrator)
CANCEL_ORCHESTRATOR = Depends(get_cancel_request_orchestrator)
SCHEDULER_STRATEGY = Depends(get_scheduler_strategy)
STATUS_QUERY = Query(None, description="Filter by request status")
LIMIT_QUERY = Query(50, description="Limit number of results")
OFFSET_QUERY = Query(0, ge=0, description="Number of results to skip")

_TERMINAL_STATUSES = {"complete", "completed", "failed", "error", "cancelled", "canceled"}


@router.get("/", summary="List Requests", description="List requests with optional filtering")
@handle_rest_exceptions(endpoint="/api/v1/requests", method="GET")
async def list_requests(
    status: Optional[str] = STATUS_QUERY,
    limit: Optional[int] = LIMIT_QUERY,
    offset: int = OFFSET_QUERY,
    sync: bool = Query(False, description="Sync with provider before returning results"),
    orchestrator=LIST_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    """
    List requests with optional filtering.

    - **status**: Filter by request status (pending, running, complete, failed)
    - **limit**: Limit number of results
    - **offset**: Number of results to skip
    - **sync**: Sync with provider before returning results
    """
    result = await orchestrator.execute(
        ListRequestsInput(status=status, limit=limit or 50, offset=offset, sync=sync)
    )
    return JSONResponse(content=scheduler.format_request_status_response(result.requests))


@router.get("/return", summary="List Return Requests", description="List requests pending return")
@handle_rest_exceptions(endpoint="/api/v1/requests/return", method="GET")
async def list_return_requests(
    limit: int = LIMIT_QUERY,
    orchestrator=RETURN_LIST_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    """List requests that are pending return."""
    result = await orchestrator.execute(ListReturnRequestsInput(limit=limit or 50))
    return JSONResponse(content=scheduler.format_request_status_response(result.requests))


@router.get(
    "/{request_id}/status",
    summary="Get Request Status",
    description="Get status of a specific request",
)
@handle_rest_exceptions(endpoint="/api/v1/requests/{request_id}/status", method="GET")
async def get_request_status(
    request_id: str,
    long: bool = Query(True, description="Include detailed info and refresh provider state"),
    orchestrator=STATUS_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    """
    Get the status of a specific request.

    - **request_id**: Request identifier
    - **long**: Include detailed information about the request
    """
    result = await orchestrator.execute(
        GetRequestStatusInput(request_ids=[request_id], detailed=long)
    )
    return JSONResponse(content=scheduler.format_request_status_response(result.requests))


@router.get(
    "/{request_id}/stream",
    summary="Stream Request Status",
    description="Stream request status updates as Server-Sent Events",
)
async def stream_request_status(
    request_id: str,
    orchestrator=STATUS_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
    interval: float = Query(2.0, ge=0.5, le=60, description="Poll interval in seconds"),
    timeout: float = Query(300.0, ge=1, le=3600, description="Max stream duration in seconds"),
) -> StreamingResponse:
    """Stream request status as SSE until terminal state or timeout."""

    async def event_generator():
        elapsed = 0.0
        while elapsed < timeout:
            try:
                result = await orchestrator.execute(
                    GetRequestStatusInput(request_ids=[request_id], detailed=False)
                )
                formatted = scheduler.format_request_status_response(result.requests)
                yield f"data: {json.dumps(formatted)}\n\n"
                requests_list = formatted.get("requests", [])
                if requests_list:
                    status = requests_list[0].get("status", "")
                    if status.lower() in _TERMINAL_STATUSES:
                        yield "data: {}\n\n"
                        return
            except Exception:
                yield "data: {}\n\n"
                return
            await asyncio.sleep(interval)
            elapsed += interval

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/{request_id}", summary="Cancel Request", description="Cancel a pending request")
@handle_rest_exceptions(endpoint="/api/v1/requests/{request_id}", method="DELETE")
async def cancel_request(
    request_id: str,
    reason: Optional[str] = Query(None, description="Cancellation reason"),
    orchestrator=CANCEL_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    result = await orchestrator.execute(
        CancelRequestInput(
            request_id=request_id,
            reason=reason or "Cancelled via REST API",
        )
    )
    return JSONResponse(content=scheduler.format_request_response(result.raw))
