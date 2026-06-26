"""Machine management API routes."""

from typing import Any, Optional

try:
    from fastapi import APIRouter, Depends, Query, Request
    from fastapi.responses import JSONResponse
    from pydantic import AliasChoices, Field
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

router = APIRouter(prefix="/machines", tags=["Machines"])

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
    """

    machine_ids: list[str]
    all_machines: bool = False
    force: bool = False


@router.post(
    "/request",
    summary="Request Machines",
    description="Request new machines from a template",
    status_code=202,
    response_model=RequestOperationResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/machines/request", method="POST")
async def request_machines(
    request_data: RequestMachinesRequest,
    _user=Depends(require_role("operator")),
    orchestrator=ACQUIRE_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    """
    Request new machines from a template.

    - **template_id**: Template to use for machine creation
    - **count**: Number of machines to request (also accepted as machine_count or machineCount)
    - **additional_data**: Optional additional configuration data
    """
    result = await orchestrator.execute(
        AcquireMachinesInput(
            template_id=request_data.template_id,
            requested_count=request_data.count,
            additional_data=request_data.additional_data or {},
        )
    )
    response_data: dict = {
        "status": result.status,
        "machine_ids": result.machine_ids,
    }
    if result.request_id:
        response_data["request_id"] = result.request_id
    return JSONResponse(
        content=formatter.format_request_operation(response_data, result.status).data,
        status_code=202,
    )


@router.post(
    "/return",
    summary="Return Machines",
    description="Return machines to the provider",
    response_model=RequestOperationResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/machines/return", method="POST")
async def return_machines(
    request_data: ReturnMachinesRequest,
    _user=Depends(require_role("operator")),
    orchestrator=RETURN_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    """
    Return machines to the provider.

    - **machine_ids**: List of machine IDs to return
    """
    result = await orchestrator.execute(
        ReturnMachinesInput(
            machine_ids=request_data.machine_ids,
            all_machines=request_data.all_machines,
            force=request_data.force,
        )
    )
    response_data: dict = {
        "status": result.status,
        "message": result.message,
        "skipped_machines": result.skipped_machines,
    }
    if result.request_id:
        response_data["request_id"] = result.request_id
    return JSONResponse(
        content=formatter.format_request_operation(response_data, result.status).data
    )


@router.get(
    "/",
    summary="List Machines",
    description="List machines with optional filtering",
    response_model=MachineListResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/machines", method="GET")
async def list_machines(
    status: Optional[str] = STATUS_QUERY,
    provider_name: Optional[str] = Query(None),
    request_id: Optional[str] = REQUEST_ID_QUERY,
    limit: int = Query(50),
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
    orchestrator=LIST_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    result = await orchestrator.execute(
        ListMachinesInput(
            status=status,
            provider_name=provider_name,
            request_id=request_id,
            limit=limit,
            offset=offset,
            cursor=cursor,
            q=q,
            sort=sort,
            sync=sync,
        )
    )
    payload = formatter.format_machine_list(result.machines).data
    if isinstance(payload, dict):
        payload = {
            **payload,
            "total_count": (
                result.total_count if result.total_count is not None else len(result.machines)
            ),
            "next_cursor": result.next_cursor,
        }
    return JSONResponse(content=payload)


@router.get(
    "/{machine_id}/status",
    summary="Sync Machine Status",
    description="Refresh a single machine from the provider and return the up-to-date DTO.",
    response_model=MachineListResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/machines/{machine_id}/status", method="GET")
async def sync_machine_status(
    machine_id: str,
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
    data = result.machine.to_dict()
    payload = formatter.format_machine_detail(data).data
    if isinstance(payload, dict):
        payload = {**payload, "synced": result.synced}
        if result.error:
            payload["sync_error"] = result.error
    return JSONResponse(content=payload)


@router.get(
    "/{machine_id}",
    summary="Get Machine",
    description="Get specific machine details",
    response_model=MachineListResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/machines/{machine_id}", method="GET")
async def get_machine(
    machine_id: str,
    orchestrator=GET_ORCHESTRATOR,
    formatter=FORMATTER,
) -> JSONResponse:
    result = await orchestrator.execute(GetMachineInput(machine_id=machine_id))
    if result.machine is None:
        return JSONResponse(content={"detail": f"Machine {machine_id} not found"}, status_code=404)
    # MachineDTO defines its own ``to_dict`` for the snake_case wire shape;
    # pydantic's ``model_dump`` would also work but ``to_dict`` is the
    # explicit API surface and matches the rest of the formatter pipeline.
    data = result.machine.to_dict()
    return JSONResponse(content=formatter.format_machine_detail(data).data)


@router.delete(
    "/{machine_id}",
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
            "machine_id": machine_id,
            "machines_deleted": cleanup_result.machines_deleted,
        },
    )
