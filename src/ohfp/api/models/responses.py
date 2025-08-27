"""Response models and formatters for API handlers."""

from typing import Any, Optional

from pydantic import BaseModel

from infrastructure.error.exception_handler import InfrastructureErrorResponse


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
        response["data"] = data
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
