"""Response models and formatters for API handlers."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from infrastructure.error.exception_handler import InfrastructureErrorResponse


def format_error_for_api(error_response: InfrastructureErrorResponse) -> Dict[str, Any]:
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


def format_success_for_api(message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Format success response for API consumption."""
    response = {"status": "success", "message": message}
    if data is not None:
        response["data"] = data
    return response


class SuccessResponse(BaseModel):
    """Model for success responses."""

    status: str = "success"
    message: str
    data: Optional[Dict[str, Any]] = None


# Backward compatibility - create error response using formatter
def create_error_response(
    message: str, errors: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Create error response for backward compatibility."""
    return {"status": "error", "message": message, "errors": errors or []}
