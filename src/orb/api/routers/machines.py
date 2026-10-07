"""Machine management API routes."""

from typing import Any, Optional

try:
    from fastapi import APIRouter, Depends, Query, Request
    from fastapi.responses import JSONResponse
    from pydantic import AliasChoices, Field, model_validator
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import (
    check_destructive_admin_allowed as _check_destructive_admin_allowed,
    get_acquire_machines_orchestrator,
    get_di_container,
    get_list_machines_orchestrator,
    get_machine_orchestrator,
    get_request_formatter,
    get_return_machines_orchestrator,
    get_sync_machine_orchestrator,
    require_role,
)
from orb.api.models.base import APIRequest
from orb.api.models.responses import MachineListResponse, RequestOperationResponse
from orb.application.services.admin.cleanup_database import (
    CleanupDatabaseService,
    NonTerminalStatusError,
)
from orb.application.services.orchestration.dtos import (
    AcquireMachinesInput,
    GetMachineInput,
    ListMachinesInput,
    ReturnMachinesInput,
    SyncMachineInput,
)
from orb.domain.base import UnitOfWorkFactory
from orb.infrastructure.error.decorators import handle_rest_exceptions
from orb.infrastructure.logging.logger import get_logger
from orb.interface.catalog import OPERATION_CATALOG, Interface

router = APIRouter(prefix="/machines", tags=["Machines"])

logger = get_logger(__name__)

# Module-level dependency variables to avoid B008 warnings
ACQUIRE_ORCHESTRATOR = Depends(get_acquire_machines_orchestrator)
RETURN_ORCHESTRATOR = Depends(get_return_machines_orchestrator)
LIST_ORCHESTRATOR = Depends(get_list_machines_orchestrator)
GET_ORCHESTRATOR = Depends(get_machine_orchestrator)
SYNC_ORCHESTRATOR = Depends(get_sync_machine_orchestrator)
FORMATTER = Depends(get_request_formatter)
STATUS_QUERY = Query(None, description="Filter by machine status")
REQUEST_ID_QUERY = Query(None, description="Filter by request ID")
OFFSET_QUERY = Query(0, ge=0, description="Number of results to skip")

# Server-side ceiling for blocking ``?wait=true`` requests, so a client cannot
# hold a worker connection open indefinitely.
_MAX_WAIT_TIMEOUT_SECONDS = 600
WAIT_QUERY = Query(False, description="Block until the request reaches a terminal state")
WAIT_TIMEOUT_QUERY = Query(
    300, ge=1, le=_MAX_WAIT_TIMEOUT_SECONDS, description="Max wait duration in seconds"
)


class RequestMachinesRequest(APIRequest):
    """Request for machine provisioning.

    Accepts count, machine_count, or machineCount in the request body.
    """

    template_id: str
    count: int = Field(validation_alias=AliasChoices("count", "machine_count", "machineCount"))
    additional_data: Optional[dict[str, Any]] = None


class ReturnMachinesRequest(APIRequest):
    """Request for machine return.

    Accepts both camelCase (machineIds) and snake_case field names.
    Exactly one of machine_ids (non-empty), request_id, or all_machines=True must be provided.
    """

    machine_ids: list[str] = Field(default_factory=list)
    request_id: Optional[str] = None
    all_machines: bool = False
    force: bool = False
    provider_name: Optional[str] = None
    provider_type: Optional[str] = None

    @model_validator(mode="after")
    def validate_target_selection(self) -> "ReturnMachinesRequest":
        has_machine_ids = bool(self.machine_ids)
        has_request_id = bool(self.request_id)
        has_all = self.all_machines
        # Targeting modes are mutually exclusive, but zero is allowed: an empty
        # request is handled downstream as a no-op (preserves existing contract).
        if sum([has_machine_ids, has_request_id, has_all]) > 1:
            raise ValueError(
                "machine_ids, request_id, and all_machines are mutually exclusive; "
                "provide at most one."
            )
        return self


@router.post(
    "/request",
    operation_id="requestMachines",
    summary="Request Machines",
    description="Request new machines from a template",
    status_code=202,
    response_model=RequestOperationResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/machines/request", method="POST")
async def request_machines(
    request_data: RequestMachinesRequest,
    wait: bool = WAIT_QUERY,
    timeout: int = WAIT_TIMEOUT_QUERY,
    _user=Depends(require_role("operator")),
    orchestrator=ACQUIRE_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    """
    Request new machines from a template.

    - **template_id**: Template to use for machine creation
    - **count**: Number of machines to request (also accepted as machine_count or machineCount)
    - **additional_data**: Optional additional configuration data
    - **wait**: Block server-side until the request is terminal or ``timeout`` elapses
    - **timeout**: Maximum seconds to block when ``wait=true`` (server caps at 600)
    """
    result = await orchestrator.execute(
        AcquireMachinesInput(
            template_id=request_data.template_id,
            requested_count=request_data.count,
            additional_data=request_data.additional_data or {},
            wait=wait,
            timeout_seconds=min(timeout, _MAX_WAIT_TIMEOUT_SECONDS),
        )
    )
    entry = OPERATION_CATALOG["request_machines"]
    body = entry.renderer_for(Interface.REST)(formatter, result).data
    return JSONResponse(content=body, status_code=202)


@router.post(
    "/return",
    operation_id="returnMachines",
    summary="Return Machines",
    description="Return machines to the provider",
    response_model=RequestOperationResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/machines/return", method="POST")
async def return_machines(
    request_data: ReturnMachinesRequest,
    wait: bool = WAIT_QUERY,
    timeout: int = WAIT_TIMEOUT_QUERY,
    _user=Depends(require_role("operator")),
    orchestrator=RETURN_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    """
    Return machines to the provider.

    - **machine_ids**: List of machine IDs to return
    - **wait**: Block server-side until the return is terminal or ``timeout`` elapses
    - **timeout**: Maximum seconds to block when ``wait=true`` (server caps at 600)
    """
    result = await orchestrator.execute(
        ReturnMachinesInput(
            machine_ids=request_data.machine_ids,
            request_id=request_data.request_id,
            all_machines=request_data.all_machines,
            force=request_data.force,
            wait=wait,
            timeout_seconds=min(timeout, _MAX_WAIT_TIMEOUT_SECONDS),
            provider_name=request_data.provider_name,
            provider_type=request_data.provider_type,
        )
    )
    entry = OPERATION_CATALOG["return_machines"]
    body = entry.renderer_for(Interface.REST)(formatter, result).data
    return JSONResponse(content=body)


@router.get(
    "/",
    operation_id="listMachines",
    summary="List Machines",
    description="List machines with optional filtering",
    response_model=MachineListResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/machines", method="GET")
async def list_machines(
    status: Optional[str] = STATUS_QUERY,
    provider_name: Optional[str] = Query(None),
    provider_type: Optional[str] = Query(None),
    request_id: Optional[str] = REQUEST_ID_QUERY,
    limit: int = Query(50, ge=1, le=1000),
    offset: int = OFFSET_QUERY,
    cursor: Optional[str] = Query(None, description="Opaque pagination cursor"),
    q: Optional[str] = Query(None, description="Substring search"),
    sort: Optional[str] = Query(None, description='Sort: "field" / "-field"'),
    sync: bool = Query(
        False,
        description=(
            "Refresh every machine on the returned page from the provider. "
            "Costly at scale (one DescribeInstances per row). Off by default; "
            "use /machines/{id}/status to refresh a single row instead."
        ),
    ),
    timestamp_format: Optional[str] = Query(None, description="Timestamp format override"),
    filter_expressions: list[str] = Query(default=[]),
    _user=Depends(require_role("viewer")),
    orchestrator=LIST_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    result = await orchestrator.execute(
        ListMachinesInput(
            status=status,
            provider_name=provider_name,
            provider_type=provider_type,
            request_id=request_id,
            limit=limit,
            offset=offset,
            cursor=cursor,
            q=q,
            sort=sort,
            sync=sync,
            timestamp_format=timestamp_format,
            filter_expressions=filter_expressions,
        )
    )
    entry = OPERATION_CATALOG["list_machines"]
    payload = entry.renderer_for(Interface.REST)(formatter, result).data
    return JSONResponse(content=payload)


@router.get(
    "/{machine_id}/status",
    operation_id="syncMachineStatus",
    summary="Sync Machine Status",
    description="Refresh a single machine from the provider and return the up-to-date DTO.",
    response_model=MachineListResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/machines/{machine_id}/status", method="GET")
async def sync_machine_status(
    machine_id: str,
    _user=Depends(require_role("viewer")),
    orchestrator=SYNC_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    """Per-machine read-through provider sync.

    Mirrors GET /requests/{id}/status. Loads the machine, asks the
    provider for live state, persists any changes, and returns the
    refreshed MachineDTO. Bounded to one DescribeInstances per call.
    """
    result = await orchestrator.execute(SyncMachineInput(machine_id=machine_id))
    if result.machine is None:
        return JSONResponse(content={"detail": f"Machine {machine_id} not found"}, status_code=404)
    # The 404 envelope above is REST-specific; the success body (machine detail
    # with the sync outcome overlaid) is the shared shape declared in the catalog.
    entry = OPERATION_CATALOG["sync_machine"]
    payload = entry.renderer_for(Interface.REST)(formatter, result).data
    return JSONResponse(content=payload)


@router.get(
    "/{machine_id}",
    operation_id="getMachine",
    summary="Get Machine",
    description="Get specific machine details",
    response_model=MachineListResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/machines/{machine_id}", method="GET")
async def get_machine(
    machine_id: str,
    _user=Depends(require_role("viewer")),
    orchestrator=GET_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    result = await orchestrator.execute(GetMachineInput(machine_id=machine_id))
    if result.machine is None:
        return JSONResponse(content={"detail": f"Machine {machine_id} not found"}, status_code=404)
    # The 404 envelope above is REST-specific; the success body is the shared
    # canonical machine detail declared in the catalog.
    entry = OPERATION_CATALOG["get_machine"]
    return JSONResponse(content=entry.renderer_for(Interface.REST)(formatter, result).data)


@router.delete(
    "/{machine_id}",
    operation_id="purgeMachine",
    summary="Purge Machine",
    description=(
        "Hard-delete a single machine row from storage. "
        "Only ?purge=true mode is supported (there is no soft-delete for machines beyond "
        "the return workflow). "
        "Requires allow_destructive_admin=true in config, non-production environment, "
        "and the machine must already be in a terminal state (terminated, failed, returned)."
    ),
)
@handle_rest_exceptions(endpoint="/api/v1/machines/{machine_id}", method="DELETE")
async def purge_machine(
    machine_id: str,
    request: Request,
    purge: bool = Query(False, description="Must be true to confirm hard-delete"),
    _user=Depends(require_role("admin")),
) -> JSONResponse:
    if not purge:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": {
                    "code": "PURGE_REQUIRED",
                    "message": (
                        "Machines have no soft-delete. Add ?purge=true to confirm hard-deletion."
                    ),
                },
            },
        )

    # Destructive-admin guard. Called inline (not via Depends) so the
    # PURGE_REQUIRED 400 above runs before this gate. handle_rest_exceptions
    # re-raises HTTPException so the 403 propagates intact.
    _check_destructive_admin_allowed(request)

    container = get_di_container()
    service = CleanupDatabaseService(uow_factory=container.get(UnitOfWorkFactory))

    try:
        cleanup_result = service.delete_machine(machine_id)
    except KeyError as exc:
        logger.warning("Machine purge failed — not found: %s", exc)
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "error": {"code": "NOT_FOUND", "message": "Machine not found."},
            },
        )
    except NonTerminalStatusError as exc:
        logger.warning("Machine purge rejected — non-terminal status: %s", exc)
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": {
                    "code": "NON_TERMINAL_STATUS",
                    "message": "Machine cannot be purged because it is not in a terminal state.",
                },
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "deleted": True,
            "machine_id": machine_id,
            "machines_deleted": cleanup_result.machines_deleted,
        },
    )
