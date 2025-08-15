"""Error response DTOs and HTTP response formatting."""

from datetime import datetime
from http import HTTPStatus
from typing import Any, Dict, Optional

from pydantic import Field

from application.dto.base import BaseDTO
from domain.base.exceptions import (
    BusinessRuleViolationError,
    ConfigurationError,
    EntityNotFoundError,
    InfrastructureError,
    ValidationError,
)

from .categories import ErrorCategory


class InfrastructureErrorResponse(BaseDTO):
    """
    Infrastructure layer error response.

    Wraps domain errors with infrastructure-specific context
    and provides formatting capabilities for different output formats.
    """

    error_code: str
    message: str
    category: str = ErrorCategory.INTERNAL
    details: Dict[str, Any] = Field(default_factory=dict)
    http_status: int = HTTPStatus.INTERNAL_SERVER_ERROR
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    @classmethod
    def from_domain_error(
        cls,
        error_code: str,
        message: str,
        category: str = ErrorCategory.INTERNAL,
        details: Optional[Dict[str, Any]] = None,
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
        cls, exception: Exception, context: Optional[str] = None
    ) -> "InfrastructureErrorResponse":
        """Create infrastructure error response from exception."""
        error_code, message, category, details = cls._exception_to_components(exception)
        http_status = cls._determine_http_status(category)

        if context:
            details["context"] = context

        return cls(
            error_code=error_code,
            message=message,
            category=category,
            details=details,
            http_status=http_status,
        )

    def to_api_response(self) -> Dict[str, Any]:
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

    def to_dict(self) -> Dict[str, Any]:
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
    ) -> tuple[str, str, str, Dict[str, Any]]:
        """Convert exception to error components."""
        if isinstance(exception, ValidationError):
            return (
                "VALIDATION_ERROR",
                str(exception),
                ErrorCategory.VALIDATION,
                getattr(exception, "details", {}),
            )
        elif isinstance(exception, EntityNotFoundError):
            return (
                "ENTITY_NOT_FOUND",
                str(exception),
                ErrorCategory.ENTITY_NOT_FOUND,
                {"entity_type": getattr(exception, "entity_type", "unknown")},
            )
        elif isinstance(exception, BusinessRuleViolationError):
            return (
                "BUSINESS_RULE_VIOLATION",
                str(exception),
                ErrorCategory.BUSINESS_RULE_VIOLATION,
                getattr(exception, "details", {}),
            )
        elif isinstance(exception, ConfigurationError):
            return (
                "CONFIGURATION_ERROR",
                str(exception),
                ErrorCategory.CONFIGURATION,
                getattr(exception, "details", {}),
            )
        elif isinstance(exception, InfrastructureError):
            return (
                "INFRASTRUCTURE_ERROR",
                str(exception),
                ErrorCategory.DATABASE_ERROR,
                getattr(exception, "details", {}),
            )
        else:
            return (
                "UNEXPECTED_ERROR",
                str(exception),
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
