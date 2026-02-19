"""Common error handling utilities.

Pure utility functions that complement ExceptionHandler with focused
functionality for error message formatting, context building, and
stack trace handling.
"""

import traceback
from datetime import datetime, timezone
from typing import Any, Optional


def format_error_message(error: Exception, include_traceback: bool = False) -> str:
    """Format error message with optional traceback.

    Args:
        error: Exception to format
        include_traceback: Whether to include stack trace

    Returns:
        Formatted error message
    """
    error_type = type(error).__name__
    error_msg = str(error) or "No message"

    # Include error code for domain exceptions
    if hasattr(error, "error_code") and error.error_code:
        base_msg = f"{error.error_code}: {error_msg}"
    else:
        base_msg = f"{error_type}: {error_msg}"

    if include_traceback:
        tb = traceback.format_exc()
        if tb.strip() == "NoneType: None":
            # No active exception, format current stack instead
            tb = "".join(traceback.format_stack())
        return f"{base_msg}\n\n{tb}"

    return base_msg


def build_error_context(error: Exception, **kwargs) -> dict[str, Any]:
    """Build error context dictionary.

    Args:
        error: Exception to build context for
        **kwargs: Additional context data

    Returns:
        Error context dictionary
    """
    context = {
        "error_type": type(error).__name__,
        "error_message": str(error),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }

    # Include error details for domain exceptions
    if hasattr(error, "details") and error.details:
        context["error_details"] = error.details

    return context


def format_stack_trace(error: Optional[Exception] = None, limit: Optional[int] = None) -> str:
    """Format stack trace for debugging.

    Args:
        error: Exception to format traceback for, or None for current stack
        limit: Maximum number of stack frames to include

    Returns:
        Formatted stack trace
    """
    if error is not None:
        # Format exception traceback
        tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
        if limit:
            # Keep first few and last few lines
            if len(tb_lines) > limit:
                keep = limit // 2
                tb_lines = tb_lines[:keep] + ["  ... (truncated) ...\n"] + tb_lines[-keep:]
        return "".join(tb_lines)
    else:
        # Format current stack
        stack = traceback.format_stack()
        if limit:
            stack = stack[-limit:]
        return f"Current stack trace:\n{''.join(stack)}"


def generate_error_code(error: Exception, prefix: Optional[str] = None) -> str:
    """Generate consistent error code from exception.

    Args:
        error: Exception to generate code for
        prefix: Optional prefix for the error code

    Returns:
        Generated error code
    """
    # Use existing error code if available
    if hasattr(error, "error_code") and error.error_code:
        base_code = error.error_code
    else:
        # Generate from exception type name
        error_type = type(error).__name__
        # Convert CamelCase to UPPER_SNAKE_CASE
        import re

        base_code = re.sub("([a-z0-9])([A-Z])", r"\1_\2", error_type).upper()

    if prefix:
        return f"{prefix}_{base_code}"

    return base_code
