"""Safe JSON utilities with comprehensive error handling.

This module provides utility functions for safe JSON operations with proper
error handling, logging, and validation. All JSON parsing operations should
use these utilities to ensure consistent error handling across the codebase.
"""

import json
from typing import Any, Optional, Union

from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


class JSONParseError(Exception):
    """Raised when JSON parsing fails."""

    def __init__(self, message: str, original_error: Optional[Exception] = None):
        super().__init__(message)
        self.original_error = original_error


def safe_json_loads(
    data: Union[str, bytes],
    default: Any = None,
    raise_on_error: bool = False,
    context: Optional[str] = None,
) -> Any:
    """Safely parse JSON string with comprehensive error handling.

    Args:
        data: JSON string or bytes to parse
        default: Default value to return on error (if raise_on_error=False)
        raise_on_error: If True, raise JSONParseError on failure
        context: Optional context string for error messages

    Returns:
        Parsed JSON object or default value on error

    Raises:
        JSONParseError: If raise_on_error=True and parsing fails
    """
    if data is None:
        if raise_on_error:
            raise JSONParseError("Cannot parse None as JSON")
        return default

    try:
        # Handle bytes input
        if isinstance(data, bytes):
            data = data.decode("utf-8")

        # Validate input is string
        if not isinstance(data, str):
            error_msg = f"Expected string or bytes, got {type(data).__name__}"
            if context:
                error_msg = f"{context}: {error_msg}"
            if raise_on_error:
                raise JSONParseError(error_msg)
            logger.warning(error_msg)
            return default

        # Parse JSON
        return json.loads(data)

    except json.JSONDecodeError as e:
        # Sanitize input for logging (truncate long strings)
        sample = data[:100] + "..." if len(data) > 100 else data  # type: ignore[operator]
        error_msg = f"JSON decode error at line {e.lineno}, col {e.colno}: {e.msg}"
        if context:
            error_msg = f"{context}: {error_msg}"

        logger.error("%s. Sample: %s", error_msg, sample, exc_info=True)

        if raise_on_error:
            raise JSONParseError(error_msg, original_error=e) from e
        return default

    except UnicodeDecodeError as e:
        error_msg = f"Unicode decode error: {e}"
        if context:
            error_msg = f"{context}: {error_msg}"

        logger.error(error_msg, exc_info=True)

        if raise_on_error:
            raise JSONParseError(error_msg, original_error=e) from e
        return default

    except Exception as e:
        error_msg = f"Unexpected error parsing JSON: {e}"
        if context:
            error_msg = f"{context}: {error_msg}"

        logger.error(error_msg, exc_info=True)

        if raise_on_error:
            raise JSONParseError(error_msg, original_error=e) from e
        return default


def safe_json_dumps(
    obj: Any,
    default: str = "{}",
    raise_on_error: bool = False,
    context: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Safely serialize object to JSON string with error handling.

    Args:
        obj: Object to serialize
        default: Default value to return on error (if raise_on_error=False)
        raise_on_error: If True, raise JSONParseError on failure
        context: Optional context string for error messages
        **kwargs: Additional arguments to pass to json.dumps

    Returns:
        JSON string or default value on error

    Raises:
        JSONParseError: If raise_on_error=True and serialization fails
    """
    try:
        return json.dumps(obj, **kwargs)

    except TypeError as e:
        error_msg = f"Type error serializing to JSON: {e}"
        if context:
            error_msg = f"{context}: {error_msg}"

        logger.error(error_msg, exc_info=True)

        if raise_on_error:
            raise JSONParseError(error_msg, original_error=e) from e
        return default

    except ValueError as e:
        error_msg = f"Value error serializing to JSON: {e}"
        if context:
            error_msg = f"{context}: {error_msg}"

        logger.error(error_msg, exc_info=True)

        if raise_on_error:
            raise JSONParseError(error_msg, original_error=e) from e
        return default

    except Exception as e:
        error_msg = f"Unexpected error serializing to JSON: {e}"
        if context:
            error_msg = f"{context}: {error_msg}"

        logger.error(error_msg, exc_info=True)

        if raise_on_error:
            raise JSONParseError(error_msg, original_error=e) from e
        return default


def validate_json_string(data: str) -> bool:
    """Validate if a string is valid JSON without parsing.

    Args:
        data: String to validate

    Returns:
        True if valid JSON, False otherwise
    """
    try:
        json.loads(data)
        return True
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
