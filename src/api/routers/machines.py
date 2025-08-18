"""Machine management API routes."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.dependencies import get_request_machines_handler, get_return_machines_handler
from infrastructure.error.decorators import handle_rest_exceptions

router = APIRouter(prefix="/machines", tags=["Machines"])

# Module-level dependency variables to avoid B008 warnings
REQUEST_MACHINES_HANDLER = Depends(get_request_machines_handler)
RETURN_MACHINES_HANDLER = Depends(get_return_machines_handler)
STATUS_QUERY = Query(None, description="Filter by machine status")
REQUEST_ID_QUERY = Query(None, description="Filter by request ID")
LIMIT_QUERY = Query(None, description="Limit number of results")


class RequestMachinesRequest(BaseModel):
    """Request for machine provisioning."""

    template_id: str
    machine_count: int
    additional_data: Optional[Dict[str, Any]] = None


class ReturnMachinesRequest(BaseModel):
    """Request for machine return."""

    machine_ids: List[str]


@router.post(
    "/request",
    summary="Request Machines",
    description="Request new machines from a template",
)
@handle_rest_exceptions(endpoint="/api/v1/machines/request", method="POST")
async def request_machines(
    request_data: RequestMachinesRequest, handler=REQUEST_MACHINES_HANDLER
) -> JSONResponse:
    """
    Request new machines from a template.

    - **template_id**: Template to use for machine creation
    - **machine_count**: Number of machines to request
    - **additional_data**: Optional additional configuration data
    """
    result = await handler.handle(
        template_id=request_data.template_id,
        machine_count=request_data.machine_count,
        input_data=request_data.additional_data,
        context={"endpoint": "/machines/request", "method": "POST"},
    )

    return JSONResponse(content=result)


@router.post("/return", summary="Return Machines", description="Return machines to the provider")
@handle_rest_exceptions(endpoint="/api/v1/machines/return", method="POST")
async def return_machines(
    request_data: ReturnMachinesRequest, handler=RETURN_MACHINES_HANDLER
) -> JSONResponse:
    """
    Return machines to the provider.

    - **machine_ids**: List of machine IDs to return
    """
    result = await handler.handle(
        machine_ids=request_data.machine_ids,
        context={"endpoint": "/machines/return", "method": "POST"},
    )

    return JSONResponse(content=result)


@router.get("/", summary="List Machines", description="List machines with optional filtering")
@handle_rest_exceptions(endpoint="/api/v1/machines", method="GET")
async def list_machines(
    status: Optional[str] = STATUS_QUERY,
    request_id: Optional[str] = REQUEST_ID_QUERY,
    limit: Optional[int] = LIMIT_QUERY,
) -> JSONResponse:
    """
    List machines with optional filtering.

    - **status**: Filter by machine status (pending, running, stopped, etc.)
    - **request_id**: Filter by request ID
    - **limit**: Limit number of results
    """
    # This would need a dedicated handler for listing machines
    # For now, return a placeholder response
    return JSONResponse(
        content={
            "success": True,
            "message": "Machine listing not yet implemented",
            "data": {
                "machines": [],
                "filters": {"status": status, "request_id": request_id, "limit": limit},
            },
        }
    )


@router.get("/{machine_id}", summary="Get Machine", description="Get specific machine details")
@handle_rest_exceptions(endpoint="/api/v1/machines/{machine_id}", method="GET")
async def get_machine(machine_id: str) -> JSONResponse:
    """
    Get specific machine details.

    - **machine_id**: Machine identifier
    """
    # This would need a dedicated handler for getting machine details
    # For now, return a placeholder response
    return JSONResponse(
        content={
            "success": True,
            "message": "Machine details not yet implemented",
            "data": {"machine_id": machine_id, "status": "unknown"},
        }
    )
