"""Machine management API routes."""

from typing import Any, Optional

try:
    from fastapi import APIRouter, Depends, Query
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import get_request_machines_handler, get_return_machines_handler
from orb.api.models.base import APIRequest
from orb.infrastructure.error.decorators import handle_rest_exceptions

router = APIRouter(prefix="/machines", tags=["Machines"])

# Module-level dependency variables to avoid B008 warnings
REQUEST_MACHINES_HANDLER = Depends(get_request_machines_handler)
RETURN_MACHINES_HANDLER = Depends(get_return_machines_handler)
STATUS_QUERY = Query(None, description="Filter by machine status")
REQUEST_ID_QUERY = Query(None, description="Filter by request ID")
LIMIT_QUERY = Query(None, description="Limit number of results")


class RequestMachinesRequest(APIRequest):
    """Request for machine provisioning.

    Accepts both camelCase (templateId, machineCount) and snake_case field names.
    """

    template_id: str
    machine_count: int
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
    - **machine_count**: Number of machines to request
    - **additional_data**: Optional additional configuration data
    """
    # Translate incoming request into the internal request model expected by the handler
    template_payload = {
        "templateId": request_data.template_id,
        "machineCount": request_data.machine_count,
    }
    if request_data.additional_data:
        template_payload.update(request_data.additional_data)

    from orb.api.models.requests import RequestMachinesModel

    request_model = RequestMachinesModel(template=template_payload)

    result = await handler.handle(request_model)

    # Let the scheduler strategy own the response format (requestId for HF, request_id for default)
    if hasattr(result, "to_dict"):
        response_content = result.to_dict()
    elif hasattr(result, "model_dump"):
        response_content = result.model_dump(by_alias=True)
    else:
        response_content = result

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
        "input_data": {"machines": [{"machineId": mid} for mid in request_data.machine_ids]},
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
    limit: Optional[int] = LIMIT_QUERY,
) -> JSONResponse:
    """
    List machines with optional filtering.

    - **status**: Filter by machine status (pending, running, stopped, etc.)
    - **request_id**: Filter by request ID
    - **limit**: Limit number of results
    """
    return JSONResponse(
        status_code=501,
        content={
            "error": "Not implemented",
            "endpoint": "GET /api/v1/machines",
            "message": (
                "Machine listing is planned but not yet available. "
                "Use GET /api/v1/requests/{request_id}/status to check provisioning status."
            ),
        },
    )


@router.get("/{machine_id}", summary="Get Machine", description="Get specific machine details")
@handle_rest_exceptions(endpoint="/api/v1/machines/{machine_id}", method="GET")
async def get_machine(machine_id: str) -> JSONResponse:
    """
    Get specific machine details.

    - **machine_id**: Machine identifier
    """
    return JSONResponse(
        status_code=501,
        content={
            "error": "Not implemented",
            "endpoint": "GET /api/v1/machines/{machine_id}",
            "message": (
                "Individual machine detail lookup is planned but not yet available. "
                "Use GET /api/v1/requests/{request_id}/status to check provisioning status."
            ),
        },
    )
