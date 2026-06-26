"""Response models and formatters for API handlers."""

from __future__ import annotations

from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict

from orb.application.request.dto import MachineReferenceDTO
from orb.infrastructure.error.exception_handler import InfrastructureErrorResponse

# ---------------------------------------------------------------------------
# Pydantic response models (snake_case, default scheduler canonical format)
# ---------------------------------------------------------------------------

# MachineRefItem was a duplicate of MachineReferenceDTO. Alias the DTO so
# the API contract layer reuses the single source of truth — adding a
# field to the DTO automatically reflects in the API schema.
MachineRefItem = MachineReferenceDTO
MachineReference = MachineReferenceDTO


class RequestItem(BaseModel):
    """
    A single request entry in a status response.

    All fields mirror RequestDTO.  Datetime fields are serialised to ISO 8601
    strings by the default scheduler formatter before being stored here.
    Derived / computed fields (is_terminal, is_failure_like, progress_percent)
    are filled in by the router layer; they default to None so the model is
    safe to construct from raw DTO output.
    """

    model_config = ConfigDict(extra="ignore")

    # --- Core identity ---
    request_id: Optional[str] = None
    status: Optional[str] = None
    request_type: Optional[str] = None
    template_id: Optional[str] = None

    # --- Counts ---
    requested_count: Optional[int] = None
    successful_count: Optional[int] = None
    failed_count: Optional[int] = None
    returned_count: Optional[int] = None          # computed by router from machine list
    desired_capacity: Optional[int] = None

    # --- Timestamps (ISO 8601 strings after scheduler serialisation) ---
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    first_status_check: Optional[str] = None
    last_status_check: Optional[str] = None

    # --- Outcome detail ---
    message: Optional[str] = None
    error_details: Optional[dict[str, Any]] = None
    success_rate: Optional[float] = None
    duration: Optional[int] = None

    # --- Machine references ---
    machines: list[MachineRefItem] = []
    machine_ids: list[str] = []

    # --- Provider info ---
    provider_api: Optional[str] = None
    provider_name: Optional[str] = None
    provider_type: Optional[str] = None
    provider_data: Optional[dict[str, Any]] = None

    # --- Resource identifiers ---
    resource_id: Optional[str] = None
    resource_ids: list[str] = []
    launch_template_id: Optional[str] = None
    launch_template_version: Optional[str] = None

    # --- Miscellaneous ---
    metadata: Optional[dict[str, Any]] = None
    version: Optional[int] = None

    # --- UI-friendly derived fields (populated server-side by router) ---
    is_terminal: Optional[bool] = None
    """True when status is one of: complete, failed, cancelled, partial, timeout."""

    is_failure_like: Optional[bool] = None
    """True when status is one of: failed, partial, timeout."""

    progress_percent: Optional[int] = None
    """int(successful_count / requested_count * 100) clamped to [0, 100]."""


class RequestStatusResponse(BaseModel):
    """Response for request status / list endpoints."""

    model_config = ConfigDict(extra="ignore")

    requests: list[RequestItem] = []
    message: Optional[str] = None
    count: Optional[int] = None
    total_count: Optional[int] = None
    next_cursor: Optional[str] = None


class RequestOperationResponse(BaseModel):
    """Response for request create / cancel operations."""

    model_config = ConfigDict(extra="ignore")

    request_id: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    warning: Optional[str] = None


class TemplateItem(BaseModel):
    """
    A single template entry.

    All fields mirror TemplateDTO.  Datetime fields are serialised to ISO 8601
    strings by the time they reach this model.
    """

    model_config = ConfigDict(extra="ignore")

    # --- Core identity ---
    template_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None

    # --- Instance configuration ---
    image_id: Optional[str] = None
    max_instances: Optional[int] = None

    # --- Machine types ---
    machine_types: Optional[dict[str, Any]] = None
    machine_types_ondemand: Optional[dict[str, Any]] = None
    machine_types_priority: Optional[dict[str, Any]] = None

    # --- Network ---
    subnet_ids: Optional[list[str]] = None
    security_group_ids: Optional[list[str]] = None
    network_zones: Optional[list[str]] = None
    public_ip_assignment: Optional[bool] = None

    # --- Pricing / allocation ---
    price_type: Optional[str] = None
    allocation_strategy: Optional[str] = None
    max_price: Optional[float] = None

    # --- Storage ---
    root_device_volume_size: Optional[int] = None
    volume_type: Optional[str] = None
    iops: Optional[int] = None
    throughput: Optional[int] = None
    storage_encryption: Optional[bool] = None
    encryption_key: Optional[str] = None

    # --- Access and security ---
    key_name: Optional[str] = None
    user_data: Optional[str] = None
    instance_profile: Optional[str] = None
    launch_template_id: Optional[str] = None

    # --- Advanced configuration ---
    monitoring_enabled: Optional[bool] = None

    # --- Tags and metadata ---
    tags: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
    provider_data: Optional[dict[str, Any]] = None

    # --- Provider configuration ---
    provider_type: Optional[str] = None
    provider_name: Optional[str] = None
    provider_api: Optional[str] = None

    # --- Timestamps (ISO 8601 strings) ---
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    # --- Active status ---
    is_active: Optional[bool] = None

    # --- Legacy ---
    version: Optional[str] = None


class TemplateListResponse(BaseModel):
    """Response for template list endpoints."""

    model_config = ConfigDict(extra="ignore")

    templates: list[TemplateItem] = []
    message: Optional[str] = None
    success: Optional[bool] = None
    total_count: Optional[int] = None
    next_cursor: Optional[str] = None


class MachineItem(BaseModel):
    """
    A single machine entry in a list response.

    All fields mirror MachineDTO plus provider_data fields surfaced at the
    top level by the default scheduler formatter.
    """

    model_config = ConfigDict(extra="ignore")

    # --- Core identity ---
    machine_id: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None
    instance_type: Optional[str] = None

    # --- Network (top-level convenience aliases kept from original model) ---
    private_ip: Optional[str] = None
    public_ip: Optional[str] = None
    private_dns_name: Optional[str] = None
    public_dns_name: Optional[str] = None

    # --- Outcome / lifecycle ---
    result: Optional[str] = None
    launch_time: Optional[Union[int, str]] = None
    termination_time: Optional[Union[int, str]] = None
    status_reason: Optional[str] = None
    message: Optional[str] = None

    # --- Request linkage ---
    request_id: Optional[str] = None
    return_request_id: Optional[str] = None
    template_id: Optional[str] = None

    # --- Network / placement (surfaced from provider_data by formatter) ---
    region: Optional[str] = None
    availability_zone: Optional[str] = None
    subnet_id: Optional[str] = None
    security_group_ids: Optional[list[str]] = None

    # --- Compute metadata ---
    vcpus: Optional[int] = None
    image_id: Optional[str] = None
    price_type: Optional[str] = None

    # --- Cloud provider fields ---
    cloud_host_id: Optional[str] = None
    resource_id: Optional[str] = None
    provider_api: Optional[str] = None
    provider_name: Optional[str] = None
    provider_type: Optional[str] = None
    provider_data: Optional[dict[str, Any]] = None

    # --- Health and observability ---
    health_checks: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None

    # --- Tags and versioning ---
    tags: Optional[Any] = None
    version: Optional[int] = None

    # --- UI-friendly derived field (populated server-side by router) ---
    uptime_seconds: Optional[int] = None
    """Seconds since launch_time when the machine is running; None otherwise."""


class MachineListResponse(BaseModel):
    """Response for machine list endpoints."""

    model_config = ConfigDict(extra="ignore")

    machines: list[MachineItem] = []
    message: Optional[str] = None
    count: Optional[int] = None
    total_count: Optional[int] = None
    next_cursor: Optional[str] = None


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


class GenerateTemplatesBody(BaseModel):
    """Request body for the POST /templates/generate endpoint."""

    provider: Optional[str] = None  # specific provider instance name
    all_providers: bool = False
    provider_api: Optional[str] = None  # e.g. "EC2Fleet"
    provider_type: Optional[str] = None  # filter by provider_type
    provider_specific: bool = False  # separate files per provider
    force: bool = False  # overwrite existing


# Backward compatibility - create error response using formatter
def create_error_response(
    message: str, errors: Optional[list[dict[str, Any]]] = None
) -> dict[str, Any]:
    """Create error response for backward compatibility."""
    return {"status": "error", "message": message, "errors": errors or []}
