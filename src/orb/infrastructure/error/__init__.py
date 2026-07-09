"""Error handling infrastructure package."""

from orb.infrastructure.error.categories import ErrorCategory, ErrorCode
from orb.infrastructure.error.error_middleware import (
    ErrorMiddleware,
    with_api_error_handling,
    with_error_handling,
)
from orb.infrastructure.error.exception_handler import ExceptionHandler, get_exception_handler
from orb.infrastructure.error.responses import ErrorResponse
from orb.infrastructure.error.utilities import (
    build_error_context,
    format_error_message,
    format_stack_trace,
    generate_error_code,
)

__all__: list[str] = [
    "ErrorCategory",
    "ErrorCode",
    "ErrorMiddleware",
    "ErrorResponse",
    "ExceptionHandler",
    "build_error_context",
    "create_error_middleware",
    "format_error_message",
    "format_stack_trace",
    "generate_error_code",
    "get_exception_handler",
    "with_api_error_handling",
    "with_error_handling",
]


def create_error_middleware() -> ErrorMiddleware:
    """
    Create and configure an error middleware.

    Returns:
        Configured ErrorMiddleware instance
    """
    return ErrorMiddleware()
