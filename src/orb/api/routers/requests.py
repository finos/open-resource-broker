"""Request management API routes."""

import asyncio
import json
from typing import Optional

try:
    from fastapi import APIRouter, Depends, Query, Request
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse, StreamingResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import (
    get_command_bus,
    get_query_bus,
    get_request_status_handler,
)
from orb.infrastructure.error.decorators import handle_rest_exceptions

router = APIRouter(prefix="/requests", tags=["Requests"])

# Module-level dependency variables to avoid B008 warnings
REQUEST_STATUS_HANDLER = Depends(get_request_status_handler)
QUERY_BUS = Depends(get_query_bus)
COMMAND_BUS = Depends(get_command_bus)
STATUS_QUERY = Query(None, description="Filter by request status")
LIMIT_QUERY = Query(50, description="Limit number of results")


@router.get(
    "/{request_id}/status",
    summary="Get Request Status",
    description="Get status of a specific request",
)
@handle_rest_exceptions(endpoint="/api/v1/requests/{request_id}/status", method="GET")
async def get_request_status(
    request_id: str,
    request: Request,
    long: bool = Query(True, description="Include detailed info and refresh provider state"),
    handler=REQUEST_STATUS_HANDLER,
) -> JSONResponse:
    """
    Get the status of a specific request.

    - **request_id**: Request identifier
    - **long**: Include detailed information about the request
    """
    api_request = {
        "input_data": {"requests": [{"requestId": request_id}]},
        "all_flag": False,
        "long": long,
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }

    result = await handler.handle(api_request)

    return JSONResponse(content=jsonable_encoder(result))


@router.get("/", summary="List Requests", description="List requests with optional filtering")
@handle_rest_exceptions(endpoint="/api/v1/requests", method="GET")
async def list_requests(
    status: Optional[str] = STATUS_QUERY,
    limit: Optional[int] = LIMIT_QUERY,
    sync: bool = Query(False, description="Sync with provider before returning results"),
    query_bus=QUERY_BUS,
) -> JSONResponse:
    """
    List requests with optional filtering.

    - **status**: Filter by request status (pending, running, complete, failed)
    - **limit**: Limit number of results
    - **sync**: Sync with provider before returning results
    """
    if sync:
        from orb.application.dto.queries import ListActiveRequestsQuery

        query = ListActiveRequestsQuery(limit=limit or 50, all_resources=True)
        results = await query_bus.execute(query)
        serialized = (
            [r.to_dict() if hasattr(r, "to_dict") else r for r in results]
            if isinstance(results, list)
            else results
        )
        return JSONResponse(content=jsonable_encoder(serialized))

    from orb.application.request.queries import ListRequestsQuery

    query = ListRequestsQuery(status=status, limit=limit or 50)
    results = await query_bus.execute(query)
    return JSONResponse(content=jsonable_encoder([r.model_dump() for r in results]))


@router.get("/return", summary="List Return Requests", description="List requests pending return")
@handle_rest_exceptions(endpoint="/api/v1/requests/return", method="GET")
async def list_return_requests(
    limit: int = LIMIT_QUERY,
    query_bus=QUERY_BUS,
) -> JSONResponse:
    """List requests that are pending return."""
    from orb.application.dto.queries import ListReturnRequestsQuery

    query = ListReturnRequestsQuery(limit=limit)
    results = await query_bus.execute(query)
    serialized = (
        [r.to_dict() if hasattr(r, "to_dict") else r for r in results]
        if isinstance(results, list)
        else results
    )
    return JSONResponse(content=jsonable_encoder(serialized))


_TERMINAL_STATUSES = {"complete", "completed", "failed", "error", "cancelled", "canceled"}


@router.get(
    "/{request_id}/stream",
    summary="Stream Request Status",
    description="Stream request status updates as Server-Sent Events",
)
async def stream_request_status(
    request_id: str,
    handler=REQUEST_STATUS_HANDLER,
    interval: float = Query(2.0, ge=0.5, le=60, description="Poll interval in seconds"),
    timeout: float = Query(300.0, ge=1, le=3600, description="Max stream duration in seconds"),
) -> StreamingResponse:
    """Stream request status as SSE until terminal state or timeout."""

    async def event_generator():
        elapsed = 0.0
        while elapsed < timeout:
            api_request = {
                "input_data": {"requests": [{"requestId": request_id}]},
                "all_flag": False,
                "long": False,
            }
            try:
                result = await handler.handle(api_request)
                if hasattr(result, "to_dict"):
                    data = result.to_dict()
                elif hasattr(result, "model_dump"):
                    data = result.model_dump()
                else:
                    data = result
                yield f"data: {json.dumps(data)}\n\n"
                requests_list = data.get("requests", [])
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
    command_bus=COMMAND_BUS,
) -> JSONResponse:
    from orb.application.dto.commands import CancelRequestCommand

    command = CancelRequestCommand(request_id=request_id, reason=reason or "Cancelled via REST API")
    await command_bus.execute(command)
    return JSONResponse(content=jsonable_encoder({"request_id": request_id, "status": "cancelled"}))


@router.get(
    "/{request_id}",
    summary="Get Request Details",
    description="Get detailed information about a request",
)
@handle_rest_exceptions(endpoint="/api/v1/requests/{request_id}", method="GET")
async def get_request_details(request_id: str, handler=REQUEST_STATUS_HANDLER) -> JSONResponse:
    """
    Get detailed information about a specific request.

    - **request_id**: Request identifier
    """
    api_request = {
        # RequestStatusModel expects a list under "requests" with requestId keys
        "input_data": {"requests": [{"requestId": request_id}]},
        "all_flag": False,
        "long": True,
        "context": {"endpoint": f"/requests/{request_id}", "method": "GET"},
    }
    result = await handler.handle(api_request)

    return JSONResponse(content=jsonable_encoder(result))
