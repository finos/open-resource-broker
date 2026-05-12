"""Response models and formatters for API handlers."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from orb.infrastructure.error.exception_handler import InfrastructureErrorResponse

# ---------------------------------------------------------------------------
# Pydantic response models (snake_case, default scheduler canonical format)
# ---------------------------------------------------------------------------


class MachineReference(BaseModel):
    """A machine reference within a request."""

    model_config = ConfigDict(extra="ignore")

    machine_id: Optional[str] = None
    name: Optional[str] = None
    result: Optional[str] = None
    status: Optional[str] = None
    private_ip_address: Optional[str] = None
    public_ip_address: Optional[str] = None
    instance_type: Optional[str] = None
    launch_time: Optional[str] = None
    message: Optional[str] = None
    cloud_host_id: Optional[str] = None
    request_id: Optional[str] = None
    return_request_id: Optional[str] = None


class RequestItem(BaseModel):
    """A single request entry in a status response."""

    model_config = ConfigDict(extra="ignore")

    request_id: Optional[str] = None
    status: Optional[str] = None
    template_id: Optional[str] = None
    requested_count: Optional[int] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    first_status_check: Optional[str] = None
    last_status_check: Optional[str] = None
    message: Optional[str] = None
    request_type: Optional[str] = None
    provider_name: Optional[str] = None
    provider_type: Optional[str] = None
    provider_api: Optional[str] = None
    machines: list[MachineReference] = []


class RequestStatusResponse(BaseModel):
    """Response for request status / list endpoints."""

    model_config = ConfigDict(extra="ignore")

    requests: list[RequestItem] = []
    message: Optional[str] = None
    count: Optional[int] = None


class RequestOperationResponse(BaseModel):
    """Response for request create / cancel operations."""

    model_config = ConfigDict(extra="ignore")

    request_id: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    warning: Optional[str] = None


class TemplateItem(BaseModel):
    """A single template entry."""

    model_config = ConfigDict(extra="ignore")

    template_id: Optional[str] = None
    name: Optional[str] = None
    image_id: Optional[str] = None
    max_instances: Optional[int] = None
    machine_types: Optional[dict[str, Any]] = None
    subnet_ids: Optional[list[str]] = None
    security_group_ids: Optional[list[str]] = None
    price_type: Optional[str] = None
    key_name: Optional[str] = None
    tags: Optional[dict[str, str]] = None
    provider_type: Optional[str] = None
    provider_api: Optional[str] = None
    is_active: Optional[bool] = None


class TemplateListResponse(BaseModel):
    """Response for template list endpoints."""

    model_config = ConfigDict(extra="ignore")

    templates: list[TemplateItem] = []
    message: Optional[str] = None
    success: Optional[bool] = None
    total_count: Optional[int] = None


class MachineItem(BaseModel):
    """A single machine entry in a list response."""

    model_config = ConfigDict(extra="ignore")

    machine_id: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None
    instance_type: Optional[str] = None
    private_ip: Optional[str] = None
    public_ip: Optional[str] = None
    result: Optional[str] = None
    launch_time: Optional[str] = None
    request_id: Optional[str] = None
    return_request_id: Optional[str] = None
    template_id: Optional[str] = None
    region: Optional[str] = None
    availability_zone: Optional[str] = None
    vcpus: Optional[int] = None
    health_checks: Optional[dict[str, Any]] = None
    cloud_host_id: Optional[str] = None


class MachineListResponse(BaseModel):
    """Response for machine list endpoints."""

    model_config = ConfigDict(extra="ignore")

    machines: list[MachineItem] = []
    message: Optional[str] = None
    count: Optional[int] = None


class TemplateMutationResponse(BaseModel):
    """Response for template create / update / delete / validate operations."""

    model_config = ConfigDict(extra="ignore")

    template_id: Optional[str] = None
    status: Optional[str] = None
    validation_errors: list[str] = []


# ---------------------------------------------------------------------------
# Legacy formatters (kept for backward compatibility)
# ---------------------------------------------------------------------------


def format_error_for_api(error_response: InfrastructureErrorResponse) -> dict[str, Any]:
    """
    Format infrastructure error response for API consumption.

    This function replaces the duplicate ErrorResponse class and provides
    a clean way to format errors for API responses.
    """
    return {
        "status": "error",
        "message": error_response.message,
        "errors": [
            {
                "code": error_response.error_code,
                "message": error_response.message,
                "category": error_response.category,
                "details": error_response.details,
            }
        ],
    }


def format_success_for_api(message: str, data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Format success response for API consumption."""
    response = {"status": "success", "message": message}
    if data is not None:
        response["data"] = data  # type: ignore[assignment]
    return response


class SuccessResponse(BaseModel):
    """Model for success responses."""

    status: str = "success"
    message: str
    data: Optional[dict[str, Any]] = None


# Backward compatibility - create error response using formatter
def create_error_response(
    message: str, errors: Optional[list[dict[str, Any]]] = None
) -> dict[str, Any]:
    """Create error response for backward compatibility."""
    return {"status": "error", "message": message, "errors": errors or []}
