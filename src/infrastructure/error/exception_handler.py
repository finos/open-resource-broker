"""
Integrated exception handling for logging, context management, and HTTP response formatting.

This integrated handler provides:
- Consistent logging across all layers
- Exception context preservation
- Generic exception wrapping
- Performance monitoring
- HTTP response formatting
- Standardized error responses

Follows DDD/SOLID/DRY principles while preserving domain exception semantics.
"""

import json
import threading
from datetime import datetime
from functools import lru_cache
from http import HTTPStatus
from typing import Any, Callable, Dict, Optional, Type

from pydantic import Field

from application.dto.base import BaseDTO
from domain.base.exceptions import (
    BusinessRuleViolationError,
    ConfigurationError,
    DomainException,
    EntityNotFoundError,
    InfrastructureError,
    ValidationError,
)
from domain.machine.exceptions import (
    MachineException,
    MachineNotFoundError,
    MachineValidationError,
)
from domain.request.exceptions import (
    RequestException,
    RequestNotFoundError,
    RequestValidationError,
)
from domain.template.exceptions import (
    TemplateException,
    TemplateNotFoundError,
    TemplateValidationError,
)
from infrastructure.logging.logger import get_logger


class ErrorCategory:
    """Error categories for classification."""

    # Domain errors
    VALIDATION = "validation_error"
    BUSINESS_RULE = "business_rule_violation"
    ENTITY_NOT_FOUND = "entity_not_found"

    # Specific not found errors
    TEMPLATE_NOT_FOUND = "template_not_found"
    MACHINE_NOT_FOUND = "machine_not_found"
    REQUEST_NOT_FOUND = "request_not_found"

    # Business rule errors
    BUSINESS_RULE_VIOLATION = "business_rule_violation"
    INVALID_STATE = "invalid_state"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"

    # Infrastructure errors
    CONFIGURATION = "configuration_error"
    DATABASE_ERROR = "database_error"
    NETWORK_ERROR = "network_error"
    EXTERNAL_SERVICE_ERROR = "external_service_error"

    # System errors
    INTERNAL_ERROR = "internal_error"
    UNEXPECTED_ERROR = "unexpected_error"

    # Legacy compatibility
    NOT_FOUND = "not_found_error"
    BUSINESS_RULE = "business_rule_error"
    INFRASTRUCTURE = "infrastructure_error"
    EXTERNAL_SERVICE = "external_service_error"
    UNAUTHORIZED = "unauthorized_error"
    FORBIDDEN = "forbidden_error"
    INTERNAL = "internal_error"


class ErrorCode:
    """Specific error codes for detailed error reporting."""

    # Validation errors
    INVALID_INPUT = "invalid_input"
    MISSING_FIELD = "missing_field"
    INVALID_FORMAT = "invalid_format"

    # Not found errors
    RESOURCE_NOT_FOUND = "resource_not_found"
    TEMPLATE_NOT_FOUND = "template_not_found"
    MACHINE_NOT_FOUND = "machine_not_found"
    REQUEST_NOT_FOUND = "request_not_found"

    # Business rule errors
    BUSINESS_RULE_VIOLATION = "business_rule_violation"
    INVALID_STATE = "invalid_state"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"

    # Infrastructure errors
    DATABASE_ERROR = "database_error"
    NETWORK_ERROR = "network_error"
    EXTERNAL_SERVICE_ERROR = "external_service_error"

    # System errors
    INTERNAL_ERROR = "internal_error"
    UNEXPECTED_ERROR = "unexpected_error"


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


class ExceptionContext:
    """Rich context information for exception handling."""

    def __init__(self, operation: str, layer: str = "application", **additional_context) -> None:
        """Initialize the instance."""
        self.operation = operation
        self.layer = layer
        self.timestamp = datetime.utcnow()
        self.thread_id = threading.get_ident()
        self.additional_context = additional_context

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary for logging."""
        return {
            "operation": self.operation,
            "layer": self.layer,
            "timestamp": self.timestamp.isoformat(),
            "thread_id": self.thread_id,
            **self.additional_context,
        }


class ExceptionHandler:
    """
    Centralized exception handling service following SRP.

    This handler routes different exception types to appropriate handlers,
    preserving domain semantics while adding consistent logging and context.
    """

    def __init__(self, logger=None, metrics=None) -> None:
        """Initialize exception handler with optional logger and metrics."""
        self.logger = logger or get_logger(__name__)
        self.metrics = metrics
        self._handlers: Dict[Type[Exception], Callable] = {}
        self._http_handlers: Dict[Type[Exception], Callable[[Exception], ErrorResponse]] = {}
        self._performance_stats = {"total_handled": 0, "by_type": {}}
        self._lock = threading.Lock()
        self._register_handlers()
        self._register_http_handlers()

    def handle(self, exception: Exception, context: ExceptionContext, **kwargs) -> Exception:
        """
        Handle exception with logging and context preservation.

        Args:
            exception: The exception to handle
            context: Rich context information
            **kwargs: Additional context data

        Returns:
            The same exception (for domain exceptions) or
            wrapped exception (for generic exceptions)
        """
        with self._lock:
            self._performance_stats["total_handled"] += 1
            exc_type = type(exception).__name__
            self._performance_stats["by_type"][exc_type] = (
                self._performance_stats["by_type"].get(exc_type, 0) + 1
            )

        # Record metrics if available
        if self.metrics:
            self.metrics.increment(f"exception.{exc_type}")
            self.metrics.increment(f"exception.layer.{context.layer}")

        # Find appropriate handler
        handler = self._get_handler(type(exception))

        # Handle with context
        return handler(exception, context, **kwargs)

    @lru_cache(maxsize=128)
    def _get_handler(self, exception_type: Type[Exception]) -> Callable:
        """
        Find the most specific handler for this exception type.

        Uses Method Resolution Order (MRO) to find the best match.
        Cached for performance.
        """
        # 1. Check for exact type match first
        if exception_type in self._handlers:
            return self._handlers[exception_type]

        # 2. Walk up inheritance hierarchy (MRO)
        for parent_type in exception_type.__mro__[1:]:  # Skip self
            if parent_type in self._handlers:
                return self._handlers[parent_type]

        # 3. Fall back to generic handler
        return self._handle_generic_exception

    def _register_handlers(self) -> None:
        """Register handlers for different exception types."""

        # DOMAIN EXCEPTIONS - Preserve with rich logging
        self._handlers[DomainException] = self._preserve_domain_exception
        self._handlers[ValidationError] = self._preserve_validation_exception
        self._handlers[EntityNotFoundError] = self._preserve_entity_not_found
        self._handlers[BusinessRuleViolationError] = self._preserve_business_rule_violation

        # TEMPLATE EXCEPTIONS - Preserve with template context
        self._handlers[TemplateException] = self._preserve_template_exception
        self._handlers[TemplateNotFoundError] = self._preserve_template_not_found
        self._handlers[TemplateValidationError] = self._preserve_template_validation

        # MACHINE EXCEPTIONS - Preserve with machine context
        self._handlers[MachineException] = self._preserve_machine_exception
        self._handlers[MachineNotFoundError] = self._preserve_machine_not_found
        self._handlers[MachineValidationError] = self._preserve_machine_validation

        # REQUEST EXCEPTIONS - Preserve with request context
        self._handlers[RequestException] = self._preserve_request_exception
        self._handlers[RequestNotFoundError] = self._preserve_request_not_found
        self._handlers[RequestValidationError] = self._preserve_request_validation

        # AWS EXCEPTIONS - Handle dynamically
        # AWS exceptions will be handled by the generic provider exception handler

        # INFRASTRUCTURE EXCEPTIONS - Preserve with infrastructure context
        self._handlers[InfrastructureError] = self._preserve_infrastructure_exception
        self._handlers[ConfigurationError] = self._preserve_configuration_exception

        # PYTHON BUILT-IN EXCEPTIONS - Wrap appropriately
        self._handlers[json.JSONDecodeError] = self._wrap_json_decode_error
        self._handlers[ConnectionError] = self._wrap_connection_error
        self._handlers[FileNotFoundError] = self._wrap_file_not_found_error
        self._handlers[ValueError] = self._wrap_value_error
        self._handlers[KeyError] = self._wrap_key_error
        self._handlers[TypeError] = self._wrap_type_error
        self._handlers[AttributeError] = self._wrap_attribute_error

    # DOMAIN EXCEPTION HANDLERS (PRESERVE)

    def _preserve_domain_exception(
        self, exc: DomainException, context: ExceptionContext, **kwargs
    ) -> DomainException:
        """Preserve domain exception with rich logging."""
        self.logger.error(
            "Domain error in %s",
            context.operation,
            extra={
                "error_code": exc.error_code,
                "error_message": exc.message,
                "error_details": exc.details,
                "context": context.to_dict(),
                "exception_type": type(exc).__name__,
                **kwargs,
            },
        )
        return exc  # Return SAME exception - preserve domain semantics

    def _preserve_validation_exception(
        self, exc: ValidationError, context: ExceptionContext, **kwargs
    ) -> ValidationError:
        """Preserve validation error with validation-specific logging."""
        self.logger.warning(
            "Validation error in %s",
            context.operation,
            extra={
                "error_code": exc.error_code,
                "validation_message": exc.message,
                "validation_details": exc.details,
                "context": context.to_dict(),
                **kwargs,
            },
        )
        return exc

    def _preserve_entity_not_found(
        self, exc: EntityNotFoundError, context: ExceptionContext, **kwargs
    ) -> EntityNotFoundError:
        """Preserve entity not found with entity-specific logging."""
        self.logger.warning(
            "Entity not found in %s",
            context.operation,
            extra={
                "error_code": exc.error_code,
                "entity_type": exc.details.get("entity_type"),
                "entity_id": exc.details.get("entity_id"),
                "context": context.to_dict(),
                **kwargs,
            },
        )
        return exc

    def _preserve_business_rule_violation(
        self, exc: BusinessRuleViolationError, context: ExceptionContext, **kwargs
    ) -> BusinessRuleViolationError:
        """Preserve business rule violation with rule-specific logging."""
        self.logger.error(
            "Business rule violation in %s",
            context.operation,
            extra={
                "error_code": exc.error_code,
                "rule_violation": exc.message,
                "rule_details": exc.details,
                "context": context.to_dict(),
                **kwargs,
            },
        )
        return exc

    # TEMPLATE EXCEPTION HANDLERS (PRESERVE)

    def _preserve_template_exception(
        self, exc: TemplateException, context: ExceptionContext, **kwargs
    ) -> TemplateException:
        """Preserve template exception with template context."""
        self.logger.error(
            "Template error in %s",
            context.operation,
            extra={
                "error_code": exc.error_code,
                "template_error": exc.message,
                "template_details": exc.details,
                "context": context.to_dict(),
                "domain": "template",
                **kwargs,
            },
        )
        return exc

    def _preserve_template_not_found(
        self, exc: TemplateNotFoundError, context: ExceptionContext, **kwargs
    ) -> TemplateNotFoundError:
        """Preserve template not found with template-specific logging."""
        self.logger.warning(
            "Template not found in %s",
            context.operation,
            extra={
                "error_code": exc.error_code,
                "template_id": exc.details.get("entity_id"),
                "context": context.to_dict(),
                "domain": "template",
                **kwargs,
            },
        )
        return exc

    def _preserve_template_validation(
        self, exc: TemplateValidationError, context: ExceptionContext, **kwargs
    ) -> TemplateValidationError:
        """Preserve template validation with validation context."""
        self.logger.warning(
            "Template validation error in %s",
            context.operation,
            extra={
                "error_code": exc.error_code,
                "validation_error": exc.message,
                "template_details": exc.details,
                "context": context.to_dict(),
                "domain": "template",
                **kwargs,
            },
        )
        return exc

    # MACHINE EXCEPTION HANDLERS (PRESERVE)

    def _preserve_machine_exception(
        self, exc: MachineException, context: ExceptionContext, **kwargs
    ) -> MachineException:
        """Preserve machine exception with machine context."""
        self.logger.error(
            "Machine error in %s",
            context.operation,
            extra={
                "error_code": exc.error_code,
                "machine_error": exc.message,
                "machine_details": exc.details,
                "context": context.to_dict(),
                "domain": "machine",
                **kwargs,
            },
        )
        return exc

    def _preserve_machine_not_found(
        self, exc: MachineNotFoundError, context: ExceptionContext, **kwargs
    ) -> MachineNotFoundError:
        """Preserve machine not found with machine-specific logging."""
        self.logger.warning(
            "Machine not found in %s",
            context.operation,
            extra={
                "error_code": exc.error_code,
                "machine_id": exc.details.get("entity_id"),
                "context": context.to_dict(),
                "domain": "machine",
                **kwargs,
            },
        )
        return exc

    def _preserve_machine_validation(
        self, exc: MachineValidationError, context: ExceptionContext, **kwargs
    ) -> MachineValidationError:
        """Preserve machine validation with validation context."""
        self.logger.warning(
            "Machine validation error in %s",
            context.operation,
            extra={
                "error_code": exc.error_code,
                "validation_error": exc.message,
                "machine_details": exc.details,
                "context": context.to_dict(),
                "domain": "machine",
                **kwargs,
            },
        )
        return exc

    # REQUEST EXCEPTION HANDLERS (PRESERVE)

    def _preserve_request_exception(
        self, exc: RequestException, context: ExceptionContext, **kwargs
    ) -> RequestException:
        """Preserve request exception with request context."""
        self.logger.error(
            "Request error in %s",
            context.operation,
            extra={
                "error_code": exc.error_code,
                "request_error": exc.message,
                "request_details": exc.details,
                "context": context.to_dict(),
                "domain": "request",
                **kwargs,
            },
        )
        return exc

    def _preserve_request_not_found(
        self, exc: RequestNotFoundError, context: ExceptionContext, **kwargs
    ) -> RequestNotFoundError:
        """Preserve request not found with request-specific logging."""
        self.logger.warning(
            "Request not found in %s",
            context.operation,
            extra={
                "error_code": exc.error_code,
                "request_id": exc.details.get("entity_id"),
                "context": context.to_dict(),
                "domain": "request",
                **kwargs,
            },
        )
        return exc

    def _preserve_request_validation(
        self, exc: RequestValidationError, context: ExceptionContext, **kwargs
    ) -> RequestValidationError:
        """Preserve request validation with validation context."""
        self.logger.warning(
            "Request validation error in %s",
            context.operation,
            extra={
                "error_code": exc.error_code,
                "validation_error": exc.message,
                "request_details": exc.details,
                "context": context.to_dict(),
                "domain": "request",
                **kwargs,
            },
        )
        return exc

    def _preserve_infrastructure_exception(
        self, exc: InfrastructureError, context: ExceptionContext, **kwargs
    ) -> InfrastructureError:
        """Preserve infrastructure exception with context."""
        self.logger.error(
            "Infrastructure error in %s",
            context.operation,
            extra={
                "error_code": getattr(exc, "error_code", "INFRASTRUCTURE_ERROR"),
                "infrastructure_error": str(exc),
                "infrastructure_details": getattr(exc, "details", {}),
                "context": context.to_dict(),
                **kwargs,
            },
        )
        return exc

    def _preserve_configuration_exception(
        self, exc: ConfigurationError, context: ExceptionContext, **kwargs
    ) -> ConfigurationError:
        """Preserve configuration exception with context."""
        self.logger.error(
            "Configuration error in %s",
            context.operation,
            extra={
                "error_code": getattr(exc, "error_code", "CONFIGURATION_ERROR"),
                "configuration_error": str(exc),
                "configuration_details": getattr(exc, "details", {}),
                "context": context.to_dict(),
                **kwargs,
            },
        )
        return exc

    # PYTHON BUILT-IN EXCEPTION HANDLERS (WRAP)

    def _wrap_json_decode_error(
        self, exc: json.JSONDecodeError, context: str = None, **kwargs
    ) -> InfrastructureError:
        """Wrap JSON decode error into appropriate domain exception based on context."""
        # Handle both string context and ExceptionContext object
        if hasattr(context, "operation"):
            context_str = context.operation
        else:
            context_str = context or ""

        context_lower = context_str.lower()

        # Context-aware exception mapping
        if "config" in context_lower or "template" in context_lower:
            return ConfigurationError(
                message=f"Invalid JSON format in {context_str or 'configuration'}: {exc.msg}",
                details={
                    "original_error": str(exc),
                    "line_number": exc.lineno,
                    "line": exc.lineno,  # For backward compatibility
                    "column_number": exc.colno,
                    "document_excerpt": exc.doc[:200] if exc.doc else None,
                    "context": context_str or "json_parsing",
                    "handler": "json_decode_error_handler",
                    "timestamp": datetime.utcnow().isoformat(),
                    **kwargs,
                },
                error_code="INVALID_JSON",  # For backward compatibility
            )
        elif "request" in context_lower or "response" in context_lower:
            return RequestValidationError(
                message=f"Invalid JSON in request data: {exc.msg}",
                details={
                    "original_error": str(exc),
                    "line_number": exc.lineno,
                    "column_number": exc.colno,
                    "context": context_str or "request_processing",
                    "handler": "json_decode_error_handler",
                    "timestamp": datetime.utcnow().isoformat(),
                    **kwargs,
                },
            )
        else:
            return InfrastructureError(
                message=f"JSON parsing failed: {exc.msg}",
                details={
                    "original_error": str(exc),
                    "line_number": exc.lineno,
                    "column_number": exc.colno,
                    "document_excerpt": exc.doc[:200] if exc.doc else None,
                    "context": context_str or "json_processing",
                    "handler": "json_decode_error_handler",
                    "timestamp": datetime.utcnow().isoformat(),
                    **kwargs,
                },
            )

    def _wrap_connection_error(
        self, exc: ConnectionError, context: str = None, **kwargs
    ) -> InfrastructureError:
        """Wrap connection error into infrastructure exception."""
        return InfrastructureError(
            message=f"Connection failed: {str(exc)}",
            details={
                "original_error": str(exc),
                "error_type": type(exc).__name__,
                "context": context or "network_operation",
                "handler": "connection_error_handler",
                "timestamp": datetime.utcnow().isoformat(),
                **kwargs,
            },
        )

    def _wrap_file_not_found_error(
        self, exc: FileNotFoundError, context: str = None, **kwargs
    ) -> InfrastructureError:
        """Wrap file not found error into appropriate domain exception."""
        context_lower = (context or "").lower()

        if "config" in context_lower or "template" in context_lower:
            return ConfigurationError(
                message=f"Required file not found: {exc.filename or 'unknown file'}",
                details={
                    "original_error": str(exc),
                    "filename": exc.filename,
                    "errno": exc.errno,
                    "context": context or "file_access",
                    "handler": "file_not_found_error_handler",
                    "timestamp": datetime.utcnow().isoformat(),
                    **kwargs,
                },
            )
        else:
            return InfrastructureError(
                message=f"File not found: {exc.filename or str(exc)}",
                details={
                    "original_error": str(exc),
                    "filename": exc.filename,
                    "errno": exc.errno,
                    "context": context or "file_operation",
                    "handler": "file_not_found_error_handler",
                    "timestamp": datetime.utcnow().isoformat(),
                    **kwargs,
                },
            )

    def _wrap_value_error(self, exc: ValueError, context: str = None, **kwargs) -> ValidationError:
        """Wrap value error into validation exception."""
        return ValidationError(
            message=f"Invalid value: {str(exc)}",
            details={
                "original_error": str(exc),
                "error_type": type(exc).__name__,
                "context": context or "value_validation",
                "handler": "value_error_handler",
                "timestamp": datetime.utcnow().isoformat(),
                **kwargs,
            },
        )

    def _wrap_key_error(self, exc: KeyError, context: str = None, **kwargs) -> ValidationError:
        """Wrap key error into validation exception."""
        return ValidationError(
            message=f"Missing required key: {str(exc)}",
            details={
                "original_error": str(exc),
                "missing_key": str(exc).strip("'\""),
                "context": context or "key_access",
                "handler": "key_error_handler",
                "timestamp": datetime.utcnow().isoformat(),
                **kwargs,
            },
        )

    def _wrap_type_error(self, exc: TypeError, context: str = None, **kwargs) -> ValidationError:
        """Wrap type error into validation exception."""
        return ValidationError(
            message=f"Type error: {str(exc)}",
            details={
                "original_error": str(exc),
                "error_type": type(exc).__name__,
                "context": context or "type_validation",
                "handler": "type_error_handler",
                "timestamp": datetime.utcnow().isoformat(),
                **kwargs,
            },
        )

    def _wrap_attribute_error(
        self, exc: AttributeError, context: str = None, **kwargs
    ) -> InfrastructureError:
        """Wrap attribute error into infrastructure exception."""
        return InfrastructureError(
            message=f"Attribute error: {str(exc)}",
            details={
                "original_error": str(exc),
                "error_type": type(exc).__name__,
                "context": context or "attribute_access",
                "handler": "attribute_error_handler",
                "timestamp": datetime.utcnow().isoformat(),
                **kwargs,
            },
        )

    def _handle_generic_exception(
        self, exc: Exception, context: str = None, **kwargs
    ) -> InfrastructureError:
        """Handle any unrecognized exception by wrapping in InfrastructureError."""
        # Handle both string context and ExceptionContext object
        if hasattr(context, "operation"):
            context_str = context.operation
        else:
            context_str = context or ""

        return InfrastructureError(
            message=f"Unexpected error: {str(exc)}",
            details={
                "original_error": str(exc),
                "error_type": type(exc).__name__,
                "context": context_str or "generic_operation",
                "handler": "generic_exception_handler",
                "timestamp": datetime.utcnow().isoformat(),
                **kwargs,
            },
        )

    # HTTP RESPONSE FORMATTING METHODS (NEW)

    def handle_error_for_http(self, exception: Exception) -> ErrorResponse:
        """Handle an exception and return a standardized HTTP error response."""
        try:
            # Get the appropriate handler for this exception type
            handler = self._get_http_handler(type(exception))
            return handler(exception)
        except Exception as e:
            self.logger.error("Error in HTTP error handler: %s", str(e))
            return self._handle_unexpected_error_http(exception)

    def _get_http_handler(
        self, exception_type: Type[Exception]
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
        self._http_handlers: Dict[
            Type[Exception], Callable[[Exception], ErrorResponse]
        ] = {  # Domain errors
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
            # Infrastructure errors (will handle AWS errors through inheritance)
            InfrastructureError: self._handle_infrastructure_error_http,
            ConfigurationError: self._handle_configuration_error_http,
        }

    # HTTP ERROR HANDLERS

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

    # AWS EXCEPTION HANDLERS (PRESERVE)


# Singleton instance for global access
_exception_handler_instance: Optional[ExceptionHandler] = None
_exception_handler_lock = threading.Lock()


def get_exception_handler() -> ExceptionHandler:
    """Get the global exception handler instance (thread-safe singleton)."""
    global _exception_handler_instance

    if _exception_handler_instance is None:
        with _exception_handler_lock:
            if _exception_handler_instance is None:
                _exception_handler_instance = ExceptionHandler()

    return _exception_handler_instance


def reset_exception_handler() -> None:
    """Reset the global exception handler (for testing)."""
    global _exception_handler_instance
    with _exception_handler_lock:
        _exception_handler_instance = None
