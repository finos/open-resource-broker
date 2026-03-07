"""Machine management API routes."""

from typing import Any, Optional

try:
    from fastapi import APIRouter, Depends, Query
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
    from pydantic import AliasChoices, Field
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import (
    get_query_bus,
    get_request_machines_handler,
    get_return_machines_handler,
)
from orb.api.models.base import APIRequest
from orb.infrastructure.error.decorators import handle_rest_exceptions

router = APIRouter(prefix="/machines", tags=["Machines"])

# Module-level dependency variables to avoid B008 warnings
REQUEST_MACHINES_HANDLER = Depends(get_request_machines_handler)
RETURN_MACHINES_HANDLER = Depends(get_return_machines_handler)
QUERY_BUS = Depends(get_query_bus)
STATUS_QUERY = Query(None, description="Filter by machine status")
REQUEST_ID_QUERY = Query(None, description="Filter by request ID")
LIMIT_QUERY = Query(None, description="Limit number of results")


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


@router.post(
    "/request",
    summary="Request Machines",
    description="Request new machines from a template",
    status_code=202,
)
@handle_rest_exceptions(endpoint="/api/v1/machines/request", method="POST")
async def request_machines(
    request_data: RequestMachinesRequest, handler=REQUEST_MACHINES_HANDLER
) -> JSONResponse:
    """
    Request new machines from a template.

    - **template_id**: Template to use for machine creation
    - **count**: Number of machines to request (also accepted as machine_count or machineCount)
    - **additional_data**: Optional additional configuration data
    """
    # Translate incoming request into the internal request model expected by the handler
    template_payload = {
        "templateId": request_data.template_id,
        "machineCount": request_data.count,
    }
    if request_data.additional_data:
        template_payload.update(request_data.additional_data)

    from orb.api.models.requests import RequestMachinesModel

    request_model = RequestMachinesModel(template=template_payload)

    result = await handler.handle(request_model)

    # Serialize DTO to snake_case then convert to camelCase at the API boundary
    if hasattr(result, "to_dict"):
        dto_dict = result.to_dict()
    elif hasattr(result, "model_dump"):
        dto_dict = result.model_dump()
    else:
        dto_dict = result

    # camelCase conversion at the REST serialization boundary
    response_content = {
        "requestId": dto_dict.get("request_id", dto_dict.get("requestId", "")),
        "message": dto_dict.get("message", ""),
    }

    return JSONResponse(content=response_content, status_code=202)


@router.post("/return", summary="Return Machines", description="Return machines to the provider")
@handle_rest_exceptions(endpoint="/api/v1/machines/return", method="POST")
async def return_machines(
    request_data: ReturnMachinesRequest, handler=RETURN_MACHINES_HANDLER
) -> JSONResponse:
    """
    Return machines to the provider.

    - **machine_ids**: List of machine IDs to return
    """
    api_request = {
        "input_data": {"machine_ids": request_data.machine_ids},
        "all_flag": False,
        "clean": False,
    }
    result = await handler.handle(api_request)

    return JSONResponse(content=jsonable_encoder(result))


@router.get("/", summary="List Machines", description="List machines with optional filtering")
@handle_rest_exceptions(endpoint="/api/v1/machines", method="GET")
async def list_machines(
    status: Optional[str] = STATUS_QUERY,
    request_id: Optional[str] = REQUEST_ID_QUERY,
    limit: int = Query(50),
    query_bus=QUERY_BUS,
) -> JSONResponse:
    from orb.application.dto.queries import ListMachinesQuery

    query = ListMachinesQuery(status=status, request_id=request_id, limit=limit)
    results = await query_bus.execute(query)
    serialized = (
        [r.to_dict() if hasattr(r, "to_dict") else r for r in results]
        if isinstance(results, list)
        else results
    )
    return JSONResponse(content=jsonable_encoder(serialized))


@router.get("/{machine_id}", summary="Get Machine", description="Get specific machine details")
@handle_rest_exceptions(endpoint="/api/v1/machines/{machine_id}", method="GET")
async def get_machine(machine_id: str, query_bus=QUERY_BUS) -> JSONResponse:
    from orb.application.dto.queries import GetMachineQuery

    query = GetMachineQuery(machine_id=machine_id)
    result = await query_bus.execute(query)
    data = result.to_dict() if hasattr(result, "to_dict") else result
    return JSONResponse(content=jsonable_encoder(data))
