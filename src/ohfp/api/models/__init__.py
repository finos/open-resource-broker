"""API models package."""

from api.models.base import (
    APIBaseModel,
    APIRequest,
    APIResponse,
    ErrorDetail,
    PaginatedResponse,
    create_request_model,
    create_response_model,
    format_api_error_response,
)
from api.models.requests import (
    BaseRequestModel,
    GetReturnRequestsModel,
    MachineReferenceModel,
    RequestMachinesModel,
    RequestReturnMachinesModel,
    RequestStatusModel,
)
from api.models.responses import (
    SuccessResponse,
    create_error_response,
    format_error_for_api,
    format_success_for_api,
)

__all__: list[str] = [
    # Base models
    "APIBaseModel",
    "APIRequest",
    "APIResponse",
    # Request models
    "BaseRequestModel",
    "ErrorDetail",
    "GetReturnRequestsModel",
    "MachineReferenceModel",
    "PaginatedResponse",
    "RequestMachinesModel",
    "RequestReturnMachinesModel",
    "RequestStatusModel",
    # Response models
    "SuccessResponse",
    "create_error_response",
    "create_request_model",
    "create_response_model",
    "format_api_error_response",
    "format_error_for_api",
    "format_success_for_api",
]
