"""Request management API routes."""

import asyncio
import json
from typing import Optional

try:
    from fastapi import APIRouter, Depends, Query, Request
    from fastapi.responses import JSONResponse, StreamingResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import (
    check_destructive_admin_allowed as _check_destructive_admin_allowed,
    get_cancel_request_orchestrator,
    get_di_container,
    get_list_requests_orchestrator,
    get_list_return_requests_orchestrator,
    get_request_formatter,
    get_request_status_orchestrator,
    require_role,
)
from orb.api.models.base import APIRequest
from orb.api.models.responses import RequestOperationResponse, RequestStatusResponse
from orb.application.services.admin.cleanup_database import (
    CleanupDatabaseService,
    NonTerminalStatusError,
)
from orb.application.services.orchestration.dtos import (
    CancelRequestInput,
    GetRequestStatusInput,
    ListRequestsInput,
    ListReturnRequestsInput,
)
from orb.domain.base import UnitOfWorkFactory
from orb.infrastructure.error.decorators import handle_rest_exceptions

router = APIRouter(prefix="/requests", tags=["Requests"])

# Module-level dependency variables to avoid B008 warnings
STATUS_ORCHESTRATOR = Depends(get_request_status_orchestrator)
LIST_ORCHESTRATOR = Depends(get_list_requests_orchestrator)
RETURN_LIST_ORCHESTRATOR = Depends(get_list_return_requests_orchestrator)
CANCEL_ORCHESTRATOR = Depends(get_cancel_request_orchestrator)
FORMATTER = Depends(get_request_formatter)
STATUS_QUERY = Query(None, description="Filter by request status")
LIMIT_QUERY = Query(50, description="Limit number of results")
OFFSET_QUERY = Query(0, ge=0, description="Number of results to skip")

_TERMINAL_STATUSES = {"complete", "completed", "failed", "error", "cancelled", "canceled"}


@router.get(
    "/",
    summary="List Requests",
    description="List requests with optional filtering",
    response_model=RequestStatusResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/requests", method="GET")
async def list_requests(
    status: Optional[str] = STATUS_QUERY,
    limit: Optional[int] = LIMIT_QUERY,
    offset: int = OFFSET_QUERY,
    sync: bool = Query(False, description="Sync with provider before returning results"),
    cursor: Optional[str] = Query(None, description="Opaque pagination cursor"),
    q: Optional[str] = Query(None, description="Substring search"),
    sort: Optional[str] = Query(None, description='Sort: "field" / "-field"'),
    orchestrator=LIST_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    """List requests with optional filtering and server-side pagination."""
    result = await orchestrator.execute(
        ListRequestsInput(
            status=status,
            limit=limit or 50,
            offset=offset,
            sync=sync,
            cursor=cursor,
            q=q,
            sort=sort,
        )
    )
    payload = formatter.format_request_status(result.requests).data
    if isinstance(payload, dict):
        payload = {
            **payload,
            "total_count": (
                result.total_count if result.total_count is not None else len(result.requests)
            ),
            "next_cursor": result.next_cursor,
        }
    return JSONResponse(content=payload)


@router.get(
    "/return",
    summary="List Return Requests",
    description="List requests pending return",
    response_model=RequestStatusResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/requests/return", method="GET")
async def list_return_requests(
    limit: int = LIMIT_QUERY,
    offset: int = OFFSET_QUERY,
    cursor: Optional[str] = Query(None, description="Opaque pagination cursor"),
    q: Optional[str] = Query(None, description="Substring search"),
    sort: Optional[str] = Query(None, description='Sort: "field" / "-field"'),
    orchestrator=RETURN_LIST_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    """List requests that are pending return."""
    result = await orchestrator.execute(
        ListReturnRequestsInput(
            limit=limit or 50,
            offset=offset,
            cursor=cursor,
            q=q,
            sort=sort,
        )
    )
    payload = formatter.format_request_status(result.requests).data
    if isinstance(payload, dict):
        payload = {
            **payload,
            "total_count": (
                result.total_count if result.total_count is not None else len(result.requests)
            ),
            "next_cursor": result.next_cursor,
        }
    return JSONResponse(content=payload)


@router.get(
    "/{request_id}/status",
    summary="Get Request Status",
    description="Get status of a specific request",
    response_model=RequestStatusResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/requests/{request_id}/status", method="GET")
async def get_request_status(
    request_id: str,
    verbose: bool = Query(True, description="Include detailed info and refresh provider state"),
    orchestrator=STATUS_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    """
    Get the status of a specific request.

    - **request_id**: Request identifier
    - **verbose**: Include detailed information about the request
    """
    result = await orchestrator.execute(
        GetRequestStatusInput(request_ids=[request_id], verbose=verbose)
    )
    return JSONResponse(content=formatter.format_request_status(result.requests).data)


class BatchRequestStatusBody(APIRequest):
    """Body for ``POST /api/v1/requests/status``.

    Accepts a list of request IDs and an optional ``verbose`` flag. The
    server fans out one read-through-sync call per ID using the same
    code path as ``GET /{id}/status`` and returns the results in the
    same order as the input.
    """

    request_ids: list[str]
    verbose: bool = True


@router.post(
    "/status",
    summary="Batch Get Request Status",
    description="Read-through-sync a batch of requests by ID.",
    response_model=RequestStatusResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/requests/status", method="POST")
async def batch_get_request_status(
    body: BatchRequestStatusBody,
    orchestrator=STATUS_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    """Sync a batch of requests from the provider.

    The orchestrator already iterates ``input.request_ids`` and persists
    each result, so this endpoint is a thin POST adapter. Failures per
    request surface as ``{"request_id": ..., "error": "..."}`` entries
    in the response list rather than failing the whole call.
    """
    result = await orchestrator.execute(
        GetRequestStatusInput(request_ids=body.request_ids, verbose=body.verbose)
    )
    return JSONResponse(content=formatter.format_request_status(result.requests).data)


@router.get(
    "/{request_id}/stream",
    summary="Stream Request Status",
    description="Stream request status updates as Server-Sent Events",
)
async def stream_request_status(
    request_id: str,
    orchestrator=STATUS_ORCHESTRATOR,
    formatter=FORMATTER,
    interval: float = Query(2.0, ge=0.5, le=60, description="Poll interval in seconds"),
    timeout: float = Query(300.0, ge=1, le=3600, description="Max stream duration in seconds"),
) -> StreamingResponse:
    """Stream request status as SSE until terminal state or timeout."""

    async def event_generator():
        elapsed = 0.0
        while elapsed < timeout:
            try:
                result = await orchestrator.execute(
                    GetRequestStatusInput(request_ids=[request_id], verbose=False)
                )
                formatted = formatter.format_request_status(result.requests).data
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


@router.delete(
    "/{request_id}",
    summary="Cancel Request",
    description="Cancel a pending request.",
    response_model=RequestOperationResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/requests/{request_id}", method="DELETE")
async def cancel_request(
    request_id: str,
    request: Request,
    reason: Optional[str] = Query(None, description="Cancellation reason"),
    _operator=Depends(require_role("operator")),
    orchestrator=CANCEL_ORCHESTRATOR,
) -> JSONResponse:
    formatter = get_request_formatter(request, get_di_container())
    result = await orchestrator.execute(
        CancelRequestInput(
            request_id=request_id,
            reason=reason or "Cancelled via REST API",
        )
    )
    return JSONResponse(
        content=formatter.format_request_operation(
            {"request_id": result.request_id, "status": result.status}, result.status
        ).data
    )


@router.post(
    "/{request_id}/purge",
    summary="Purge Request",
    description=(
        "Hard-delete a request row from storage. Requires "
        "allow_destructive_admin=true in config, a non-production environment, "
        "and the request must already be in a terminal state."
    ),
)
async def purge_request(
    request_id: str,
    request: Request,
    _admin=Depends(require_role("admin")),
    _destructive=Depends(_check_destructive_admin_allowed),
) -> JSONResponse:
    container = get_di_container()
    service = CleanupDatabaseService(uow_factory=container.get(UnitOfWorkFactory))

    try:
        cleanup_result = service.delete_request(request_id, cascade_machines=True)
    except KeyError as exc:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": {"code": "NOT_FOUND", "message": str(exc)}},
        )
    except NonTerminalStatusError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": {"code": "NON_TERMINAL_STATUS", "message": str(exc)},
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "deleted": True,
            "request_id": request_id,
            "machines_deleted": cleanup_result.machines_deleted,
        },
    )
