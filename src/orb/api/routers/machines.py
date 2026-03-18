"""Machine management API routes."""

from typing import Any, Optional

try:
    from fastapi import APIRouter, Depends, Query
    from fastapi.responses import JSONResponse
    from pydantic import AliasChoices, Field
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import (
    get_acquire_machines_orchestrator,
    get_list_machines_orchestrator,
    get_machine_orchestrator,
    get_return_machines_orchestrator,
    get_scheduler_strategy,
)
from orb.api.models.base import APIRequest
from orb.application.services.orchestration.dtos import (
    AcquireMachinesInput,
    GetMachineInput,
    ListMachinesInput,
    ReturnMachinesInput,
)
from orb.infrastructure.error.decorators import handle_rest_exceptions

router = APIRouter(prefix="/machines", tags=["Machines"])

# Module-level dependency variables to avoid B008 warnings
ACQUIRE_ORCHESTRATOR = Depends(get_acquire_machines_orchestrator)
RETURN_ORCHESTRATOR = Depends(get_return_machines_orchestrator)
LIST_ORCHESTRATOR = Depends(get_list_machines_orchestrator)
GET_ORCHESTRATOR = Depends(get_machine_orchestrator)
SCHEDULER_STRATEGY = Depends(get_scheduler_strategy)
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
)
@handle_rest_exceptions(endpoint="/api/v1/machines/request", method="POST")
async def request_machines(
    request_data: RequestMachinesRequest,
    orchestrator=ACQUIRE_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
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
    return JSONResponse(
        content=scheduler.format_request_response(result.raw),
        status_code=202,
    )


@router.post("/return", summary="Return Machines", description="Return machines to the provider")
@handle_rest_exceptions(endpoint="/api/v1/machines/return", method="POST")
async def return_machines(
    request_data: ReturnMachinesRequest,
    orchestrator=RETURN_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
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
    return JSONResponse(content=scheduler.format_request_response(result.raw))


@router.get("/", summary="List Machines", description="List machines with optional filtering")
@handle_rest_exceptions(endpoint="/api/v1/machines", method="GET")
async def list_machines(
    status: Optional[str] = STATUS_QUERY,
    request_id: Optional[str] = REQUEST_ID_QUERY,
    limit: int = Query(50),
    offset: int = OFFSET_QUERY,
    orchestrator=LIST_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    result = await orchestrator.execute(
        ListMachinesInput(status=status, request_id=request_id, limit=limit, offset=offset)
    )
    return JSONResponse(content=scheduler.format_machine_status_response(result.machines))


@router.get("/{machine_id}", summary="Get Machine", description="Get specific machine details")
@handle_rest_exceptions(endpoint="/api/v1/machines/{machine_id}", method="GET")
async def get_machine(
    machine_id: str,
    orchestrator=GET_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    result = await orchestrator.execute(GetMachineInput(machine_id=machine_id))
    if result.machine is None:
        return JSONResponse(content={"detail": f"Machine {machine_id} not found"}, status_code=404)
    data = result.machine.model_dump()
    return JSONResponse(content=scheduler.format_machine_details_response(data))
