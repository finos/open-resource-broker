"""Error handling infrastructure package."""

from infrastructure.error.error_middleware import (
    ErrorMiddleware,
    with_api_error_handling,
    with_error_handling,
)
from infrastructure.error.exception_handler import (
    ErrorCategory,
    ErrorCode,
    ErrorResponse,
    ExceptionHandler,
    get_exception_handler,
)

__all__: list[str] = [
    "ExceptionHandler",
    "ErrorResponse",
    "ErrorCategory",
    "ErrorCode",
    "ErrorMiddleware",
    "with_error_handling",
    "with_api_error_handling",
    "get_exception_handler",
    "create_error_middleware",
]


def create_error_middleware() -> ErrorMiddleware:
    """
    Create and configure an error middleware.

    Returns:
        Configured ErrorMiddleware instance
    """
    return ErrorMiddleware()
