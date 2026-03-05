"""
HTTP error response handler for standardized HTTP error responses.

Extracted from ExceptionHandler to separate HTTP concerns from general exception handling.
Provides consistent HTTP status codes and response formatting for all exception types.
"""

from http import HTTPStatus
from typing import Callable

from domain.base.exceptions import (
    BusinessRuleViolationError,
    ConfigurationError,
    EntityNotFoundError,
    InfrastructureError,
    ValidationError,
)
from domain.machine.exceptions import (
    MachineNotFoundError,
    MachineValidationError,
)
from domain.request.exceptions import (
    RequestNotFoundError,
    RequestValidationError,
)
from domain.template.exceptions import (
    TemplateNotFoundError,
    TemplateValidationError,
)
from infrastructure.error.exception_handler import ErrorCategory, ErrorCode, ErrorResponse


class HTTPErrorResponseHandler:
    """
    Handles HTTP error response formatting for all exception types.

    Provides consistent HTTP status codes and response structures
    while maintaining domain exception semantics.
    """

    def __init__(self) -> None:
        """Initialize HTTP error response handler."""
        self._http_handlers: dict[type[Exception], Callable[[Exception], ErrorResponse]] = {}  # type: ignore[misc]
        self._register_http_handlers()

    def handle_error_for_http(self, exception: Exception) -> ErrorResponse:
        """Handle an exception and return a standardized HTTP error response."""
        handler = self._get_http_handler(type(exception))
        return handler(exception)

    def _get_http_handler(
        self, exception_type: type[Exception]
    ) -> Callable[[Exception], ErrorResponse]:
        """Get the appropriate HTTP handler for an exception type."""
        # Check for exact match first
        if exception_type in self._http_handlers:
            return self._http_handlers[exception_type]

        # Check inheritance hierarchy
        for exc_type, handler in self._http_handlers.items():
            if issubclass(exception_type, exc_type):
                return handler

        # Default handler
        return self._handle_unexpected_error_http

    def _register_http_handlers(self) -> None:
        """Register HTTP error handlers."""
        self._http_handlers = {  # type: ignore[misc]
            # Domain errors
            ValidationError: self._handle_validation_error_http,
            EntityNotFoundError: self._handle_not_found_error_http,
            BusinessRuleViolationError: self._handle_business_rule_error_http,
            # Request errors
            RequestNotFoundError: self._handle_request_not_found_http,
            RequestValidationError: self._handle_request_validation_http,
            # Machine errors
            MachineNotFoundError: self._handle_machine_not_found_http,
            MachineValidationError: self._handle_machine_validation_http,
            # Template errors
            TemplateNotFoundError: self._handle_template_not_found_http,
            TemplateValidationError: self._handle_template_validation_http,
            # Infrastructure errors
            InfrastructureError: self._handle_infrastructure_error_http,
            ConfigurationError: self._handle_configuration_error_http,
        }

    def _handle_validation_error_http(self, exception: ValidationError) -> ErrorResponse:
        """Handle validation errors for HTTP responses."""
        return ErrorResponse(
            error_code=ErrorCode.INVALID_INPUT,
            message=str(exception),
            category=ErrorCategory.VALIDATION,
            details=getattr(exception, "details", {}),
            http_status=HTTPStatus.BAD_REQUEST,
        )

    def _handle_not_found_error_http(self, exception: EntityNotFoundError) -> ErrorResponse:
        """Handle not found errors for HTTP responses."""
        return ErrorResponse(
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
            message=str(exception),
            category=ErrorCategory.NOT_FOUND,
            details=getattr(exception, "details", {}),
            http_status=HTTPStatus.NOT_FOUND,
        )

    def _handle_business_rule_error_http(
        self, exception: BusinessRuleViolationError
    ) -> ErrorResponse:
        """Handle business rule violations for HTTP responses."""
        return ErrorResponse(
            error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
            message=str(exception),
            category=ErrorCategory.BUSINESS_RULE,
            details=getattr(exception, "details", {}),
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        )

    def _handle_request_not_found_http(self, exception: RequestNotFoundError) -> ErrorResponse:
        """Handle request not found errors for HTTP responses."""
        return ErrorResponse(
            error_code=ErrorCode.REQUEST_NOT_FOUND,
            message=str(exception),
            category=ErrorCategory.NOT_FOUND,
            details=getattr(exception, "details", {}),
            http_status=HTTPStatus.NOT_FOUND,
        )

    def _handle_request_validation_http(self, exception: RequestValidationError) -> ErrorResponse:
        """Handle request validation errors for HTTP responses."""
        return ErrorResponse(
            error_code=ErrorCode.INVALID_INPUT,
            message=str(exception),
            category=ErrorCategory.VALIDATION,
            details=getattr(exception, "details", {}),
            http_status=HTTPStatus.BAD_REQUEST,
        )

    def _handle_machine_not_found_http(self, exception: MachineNotFoundError) -> ErrorResponse:
        """Handle machine not found errors for HTTP responses."""
        return ErrorResponse(
            error_code=ErrorCode.MACHINE_NOT_FOUND,
            message=str(exception),
            category=ErrorCategory.NOT_FOUND,
            details=getattr(exception, "details", {}),
            http_status=HTTPStatus.NOT_FOUND,
        )

    def _handle_machine_validation_http(self, exception: MachineValidationError) -> ErrorResponse:
        """Handle machine validation errors for HTTP responses."""
        return ErrorResponse(
            error_code=ErrorCode.INVALID_INPUT,
            message=str(exception),
            category=ErrorCategory.VALIDATION,
            details=getattr(exception, "details", {}),
            http_status=HTTPStatus.BAD_REQUEST,
        )

    def _handle_template_not_found_http(self, exception: TemplateNotFoundError) -> ErrorResponse:
        """Handle template not found errors for HTTP responses."""
        return ErrorResponse(
            error_code=ErrorCode.TEMPLATE_NOT_FOUND,
            message=str(exception),
            category=ErrorCategory.NOT_FOUND,
            details=getattr(exception, "details", {}),
            http_status=HTTPStatus.NOT_FOUND,
        )

    def _handle_template_validation_http(self, exception: TemplateValidationError) -> ErrorResponse:
        """Handle template validation errors for HTTP responses."""
        return ErrorResponse(
            error_code=ErrorCode.INVALID_INPUT,
            message=str(exception),
            category=ErrorCategory.VALIDATION,
            details=getattr(exception, "details", {}),
            http_status=HTTPStatus.BAD_REQUEST,
        )

    def _handle_infrastructure_error_http(self, exception: InfrastructureError) -> ErrorResponse:
        """Handle infrastructure errors for HTTP responses."""
        return ErrorResponse(
            error_code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            message="An infrastructure error occurred",
            category=ErrorCategory.INFRASTRUCTURE,
            details={"original_error": str(exception)},
            http_status=HTTPStatus.SERVICE_UNAVAILABLE,
        )

    def _handle_configuration_error_http(self, exception: ConfigurationError) -> ErrorResponse:
        """Handle configuration errors for HTTP responses."""
        return ErrorResponse(
            error_code=ErrorCode.INTERNAL_ERROR,
            message="A configuration error occurred",
            category=ErrorCategory.INTERNAL,
            details={"original_error": str(exception)},
            http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    def _handle_unexpected_error_http(self, exception: Exception) -> ErrorResponse:
        """Handle unexpected errors for HTTP responses."""
        return ErrorResponse(
            error_code=ErrorCode.UNEXPECTED_ERROR,
            message="An unexpected error occurred",
            category=ErrorCategory.INTERNAL,
            details={"error_type": type(exception).__name__},
            http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
