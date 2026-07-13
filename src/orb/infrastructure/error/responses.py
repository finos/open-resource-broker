"""Error response DTOs and HTTP response formatting."""

from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any, Optional

from pydantic import Field

from orb.application.dto.base import BaseDTO
from orb.domain.base.exceptions import (
    BusinessRuleViolationError,
    ConfigurationError,
    EntityNotFoundError,
    InfrastructureError,
    ValidationError,
)

from .categories import ErrorCategory

# Keys that are safe to forward to callers.  All other keys are stripped so
# that wrap-chain internals (original_error, errno, filename, …) never reach
# the wire.
_SAFE_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "entity_type",
        "entity_id",
        "field",
        "field_name",
        "rule",
        "expected_version",
        "new_version",
        "current_state",
        "attempted_state",
    }
)


def _safe_details(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *raw* containing only the keys safe to send to callers."""
    return {k: v for k, v in raw.items() if k in _SAFE_DETAIL_KEYS}


class InfrastructureErrorResponse(BaseDTO):
    """
    Infrastructure layer error response.

    Wraps domain errors with infrastructure-specific context
    and provides formatting capabilities for different output formats.
    """

    error_code: str
    message: str
    category: str = ErrorCategory.INTERNAL
    details: dict[str, Any] = Field(default_factory=dict)
    http_status: int = HTTPStatus.INTERNAL_SERVER_ERROR
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_domain_error(
        cls,
        error_code: str,
        message: str,
        category: str = ErrorCategory.INTERNAL,
        details: Optional[dict[str, Any]] = None,
        http_status: Optional[int] = None,
    ) -> "InfrastructureErrorResponse":
        """Create infrastructure error response from domain error components."""
        if http_status is None:
            http_status = cls._determine_http_status(category)

        return cls(
            error_code=error_code,
            message=message,
            category=category,
            details=details or {},
            http_status=http_status,
        )

    @classmethod
    def from_exception(
        cls,
        exception: Exception,
        context: Optional[str] = None,
    ) -> "InfrastructureErrorResponse":
        """Create infrastructure error response from exception.

        The context parameter is accepted for API compatibility but is not
        forwarded to the caller - internal context strings must not reach the
        wire.
        """
        error_code, message, category, details = cls._exception_to_components(exception)
        http_status = cls._determine_http_status(category)

        return cls(
            error_code=error_code,
            message=message,
            category=category,
            details=details,
            http_status=http_status,
        )

    def to_api_response(self) -> dict[str, Any]:
        """Convert to API response format."""
        return {
            "error": {
                "code": self.error_code,
                "message": self.message,
                "category": self.category,
                "details": self.details,
            },
            "status": "error",
            "timestamp": self.timestamp.isoformat(),
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert error response to dictionary."""
        return {
            "error": {
                "code": self.error_code,
                "message": self.message,
                "category": self.category,
                "details": self.details,
            },
            "status": self.http_status,
            "timestamp": self.timestamp.isoformat(),
        }

    @staticmethod
    def _exception_to_components(
        exception: Exception,
    ) -> tuple[str, str, str, dict[str, Any]]:
        """Convert exception to error components.

        Messages are intentionally categorical - never str(exception) - so
        that internal details (host names, SQL queries, file paths) do not
        reach callers.
        """
        if isinstance(exception, ValidationError):
            return (
                "VALIDATION_ERROR",
                "Invalid input",
                ErrorCategory.VALIDATION,
                _safe_details(getattr(exception, "details", {})),
            )
        elif isinstance(exception, EntityNotFoundError):
            return (
                "ENTITY_NOT_FOUND",
                "Resource not found",
                ErrorCategory.ENTITY_NOT_FOUND,
                _safe_details({"entity_type": getattr(exception, "entity_type", "unknown")}),
            )
        elif isinstance(exception, BusinessRuleViolationError):
            return (
                "BUSINESS_RULE_VIOLATION",
                "Request could not be processed",
                ErrorCategory.BUSINESS_RULE_VIOLATION,
                _safe_details(getattr(exception, "details", {})),
            )
        elif isinstance(exception, ConfigurationError):
            return (
                "CONFIGURATION_ERROR",
                "A configuration error occurred",
                ErrorCategory.CONFIGURATION,
                {},
            )
        elif isinstance(exception, InfrastructureError):
            return (
                "INFRASTRUCTURE_ERROR",
                "An infrastructure error occurred",
                ErrorCategory.DATABASE_ERROR,
                {},
            )
        else:
            return (
                "UNEXPECTED_ERROR",
                "An unexpected error occurred",
                ErrorCategory.UNEXPECTED_ERROR,
                {"exception_type": type(exception).__name__},
            )

    @staticmethod
    def _determine_http_status(category: str) -> int:
        """Determine HTTP status code from error category."""
        category_to_status = {
            ErrorCategory.VALIDATION: HTTPStatus.BAD_REQUEST,
            ErrorCategory.ENTITY_NOT_FOUND: HTTPStatus.NOT_FOUND,
            ErrorCategory.TEMPLATE_NOT_FOUND: HTTPStatus.NOT_FOUND,
            ErrorCategory.MACHINE_NOT_FOUND: HTTPStatus.NOT_FOUND,
            ErrorCategory.REQUEST_NOT_FOUND: HTTPStatus.NOT_FOUND,
            ErrorCategory.BUSINESS_RULE_VIOLATION: HTTPStatus.UNPROCESSABLE_ENTITY,
            ErrorCategory.DUPLICATE: HTTPStatus.CONFLICT,
            ErrorCategory.INVALID_STATE: HTTPStatus.CONFLICT,
            ErrorCategory.OPERATION_NOT_ALLOWED: HTTPStatus.FORBIDDEN,
            ErrorCategory.CONFIGURATION: HTTPStatus.INTERNAL_SERVER_ERROR,
            ErrorCategory.DATABASE_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,
            ErrorCategory.NETWORK_ERROR: HTTPStatus.BAD_GATEWAY,
            ErrorCategory.EXTERNAL_SERVICE_ERROR: HTTPStatus.BAD_GATEWAY,
            ErrorCategory.INTERNAL_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,
            ErrorCategory.UNEXPECTED_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,
        }
        return category_to_status.get(category, HTTPStatus.INTERNAL_SERVER_ERROR)


# Backward compatibility alias
ErrorResponse = InfrastructureErrorResponse
