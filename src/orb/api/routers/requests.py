"""Request management API routes."""

import asyncio
import json
from typing import Optional

try:
    from fastapi import APIRouter, Depends, Query, Request
    from fastapi.responses import JSONResponse, StreamingResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

import logging as _logging

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
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.request.request_types import RequestStatus
from orb.infrastructure.error.decorators import handle_rest_exceptions
from orb.interface.catalog import OPERATION_CATALOG, Interface

router = APIRouter(prefix="/requests", tags=["Requests"])

_logger = _logging.getLogger(__name__)


def _is_not_found_marker(entry: dict) -> bool:
    """Return True when ``entry`` is the orchestrator's synthetic not-found marker.

    When a per-ID lookup raises EntityNotFoundError (e.g. RequestNotFoundError)
    the orchestrator swallows it into a synthetic dict carrying an explicit
    ``"not_found": True`` flag so batch callers can report partial failures. Any
    OTHER per-ID error (a request that exists but whose provider sync failed,
    e.g. ProviderContractError) produces an ``"error"`` entry WITHOUT that flag.
    A real request DTO never carries a "not_found" key, so the flag is an
    unambiguous discriminator: only entries explicitly marked not_found mean the
    request does not exist and should surface a 404.
    """
    return bool(entry.get("not_found"))


# Module-level dependency variables to avoid B008 warnings
STATUS_ORCHESTRATOR = Depends(get_request_status_orchestrator)
LIST_ORCHESTRATOR = Depends(get_list_requests_orchestrator)
RETURN_LIST_ORCHESTRATOR = Depends(get_list_return_requests_orchestrator)
CANCEL_ORCHESTRATOR = Depends(get_cancel_request_orchestrator)
FORMATTER = Depends(get_request_formatter)
STATUS_QUERY = Query(None, description="Filter by request status")
LIMIT_QUERY = Query(50, ge=1, le=1000, description="Limit number of results")
OFFSET_QUERY = Query(0, ge=0, description="Number of results to skip")

# Server-side ceiling for blocking ``?wait=true`` requests. Callers may pass a
# smaller ``timeout`` but never a larger one, so a client cannot hold a worker
# connection open indefinitely.
_MAX_WAIT_TIMEOUT_SECONDS = 600


def _is_terminal_status(status: str) -> bool:
    """Return True when *status* is a terminal RequestStatus value.

    Unknown strings (not in the enum) are treated as non-terminal so the SSE
    stream does not close prematurely on unexpected provider-side values.
    """
    try:
        return RequestStatus(status.lower()).is_terminal()
    except ValueError:
        _logger.debug("Unrecognised request status %r — treating as non-terminal", status)
        return False


@router.get(
    "/",
    operation_id="listRequests",
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
    provider_name: Optional[str] = Query(None, description="Filter by provider instance name"),
    provider_type: Optional[str] = Query(None, description="Filter by provider type"),
    template_id: Optional[str] = Query(None, description="Filter by template ID"),
    request_type: Optional[str] = Query(None, description="Filter by request type"),
    filter_expressions: list[str] = Query(default=[]),
    _user=Depends(require_role("viewer")),
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
            provider_name=provider_name,
            provider_type=provider_type,
            template_id=template_id,
            request_type=request_type,
            filter_expressions=filter_expressions,
        )
    )
    entry = OPERATION_CATALOG["list_requests"]
    payload = entry.renderer_for(Interface.REST)(formatter, result).data
    return JSONResponse(content=payload)


@router.get(
    "/return",
    operation_id="listReturnRequests",
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
    provider_name: Optional[str] = Query(None, description="Filter by provider instance name"),
    provider_type: Optional[str] = Query(None, description="Filter by provider type"),
    filter_expressions: list[str] = Query(default=[]),
    _user=Depends(require_role("viewer")),
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
            provider_name=provider_name,
            provider_type=provider_type,
            filter_expressions=filter_expressions,
        )
    )
    entry = OPERATION_CATALOG["list_return_requests"]
    payload = entry.renderer_for(Interface.REST)(formatter, result).data
    return JSONResponse(content=payload)


@router.get(
    "/{request_id}",
    operation_id="getRequest",
    summary="Get Request",
    description=(
        "Fetch a single request by ID and return its full status snapshot. "
        "Equivalent to GET /{request_id}/status with verbose=True."
    ),
    response_model=RequestStatusResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/requests/{request_id}", method="GET")
async def get_request(
    request_id: str,
    verbose: bool = Query(True, description="Include detailed info and refresh provider state"),
    _user=Depends(require_role("viewer")),
    orchestrator=STATUS_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    """
    Get a single request by ID.

    Delegates to the same orchestrator as GET /{request_id}/status so the
    response shape is identical. The Go SDK's GetRequest method targets this
    route.

    - **request_id**: Request identifier
    - **verbose**: Include detailed information about the request
    """
    result = await orchestrator.execute(
        GetRequestStatusInput(request_ids=[request_id], verbose=verbose)
    )
    # The orchestrator tags a genuinely non-existent request with an explicit
    # "not_found" flag (EntityNotFoundError swallowed for batch partial-failure
    # reporting). Only that flag means the request does not exist, so surface a
    # 404. An existing request whose provider sync errored comes back as an
    # error entry WITHOUT the flag and must be returned as 200 — as must a real
    # request that failed provisioning (status="failed" with an error block).
    if not result.requests or (
        len(result.requests) == 1 and _is_not_found_marker(result.requests[0])
    ):
        raise EntityNotFoundError("Request", request_id)
    entry = OPERATION_CATALOG["get_request_status"]
    return JSONResponse(content=entry.renderer_for(Interface.REST)(formatter, result).data)


@router.get(
    "/{request_id}/status",
    operation_id="getRequestStatus",
    summary="Get Request Status",
    description="Get status of a specific request",
    response_model=RequestStatusResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/requests/{request_id}/status", method="GET")
async def get_request_status(
    request_id: str,
    verbose: bool = Query(True, description="Include detailed info and refresh provider state"),
    wait: bool = Query(False, description="Block until the request reaches a terminal state"),
    timeout: int = Query(
        300, ge=1, le=_MAX_WAIT_TIMEOUT_SECONDS, description="Max wait duration in seconds"
    ),
    _user=Depends(require_role("viewer")),
    orchestrator=STATUS_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    """
    Get the status of a specific request.

    - **request_id**: Request identifier
    - **verbose**: Include detailed information about the request
    - **wait**: Poll server-side until the request is terminal or ``timeout`` elapses
    - **timeout**: Maximum seconds to block when ``wait=true`` (server caps at 600)
    """
    result = await orchestrator.execute(
        GetRequestStatusInput(
            request_ids=[request_id],
            verbose=verbose,
            wait=wait,
            timeout_seconds=min(timeout, _MAX_WAIT_TIMEOUT_SECONDS),
        )
    )
    # The orchestrator tags a genuinely non-existent request with an explicit
    # "not_found" flag (EntityNotFoundError swallowed for batch partial-failure
    # reporting). Only that flag means the request does not exist, so surface a
    # 404. An existing request whose provider sync errored comes back as an
    # error entry WITHOUT the flag and must be returned as 200 — as must a real
    # request that failed provisioning (status="failed" with an error block).
    if not result.requests or (
        len(result.requests) == 1 and _is_not_found_marker(result.requests[0])
    ):
        raise EntityNotFoundError("Request", request_id)
    entry = OPERATION_CATALOG["get_request_status"]
    return JSONResponse(content=entry.renderer_for(Interface.REST)(formatter, result).data)


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
    operation_id="batchGetRequestStatus",
    summary="Batch Get Request Status",
    description="Read-through-sync a batch of requests by ID.",
    response_model=RequestStatusResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/requests/status", method="POST")
async def batch_get_request_status(
    body: BatchRequestStatusBody,
    _user=Depends(require_role("viewer")),
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
    entry = OPERATION_CATALOG["get_request_status"]
    return JSONResponse(content=entry.renderer_for(Interface.REST)(formatter, result).data)


@router.get(
    "/{request_id}/stream",
    operation_id="streamRequest",
    summary="Stream Request Status",
    description="Stream request status updates as Server-Sent Events",
)
async def stream_request_status(
    request_id: str,
    _user=Depends(require_role("viewer")),
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
                entry = OPERATION_CATALOG["get_request_status"]
                formatted = entry.renderer_for(Interface.REST)(formatter, result).data
                yield f"data: {json.dumps(formatted)}\n\n"
                requests_list = formatted.get("requests", [])
                if requests_list:
                    status = requests_list[0].get("status", "")
                    if _is_terminal_status(status):
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
    operation_id="cancelRequest",
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
    entry = OPERATION_CATALOG["cancel_request"]
    return JSONResponse(content=entry.renderer_for(Interface.REST)(formatter, result).data)


@router.post(
    "/{request_id}/purge",
    operation_id="purgeRequest",
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
        _logger.warning("Request purge failed — not found: %s", exc)
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "error": {"code": "NOT_FOUND", "message": "Request not found."},
            },
        )
    except NonTerminalStatusError as exc:
        _logger.warning("Request purge rejected — non-terminal status: %s", exc)
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": {
                    "code": "NON_TERMINAL_STATUS",
                    "message": "Request cannot be purged because it is not in a terminal state.",
                },
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
