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

__all__ = [
    # Base models
    "APIBaseModel",
    "APIRequest",
    "APIResponse",
    "PaginatedResponse",
    "ErrorDetail",
    "format_api_error_response",
    "create_request_model",
    "create_response_model",
    # Request models
    "BaseRequestModel",
    "MachineReferenceModel",
    "RequestMachinesModel",
    "RequestStatusModel",
    "RequestReturnMachinesModel",
    "GetReturnRequestsModel",
    # Response models
    "SuccessResponse",
    "format_error_for_api",
    "format_success_for_api",
    "create_error_response",
]
