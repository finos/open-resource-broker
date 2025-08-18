"""Base API handler implementation."""

import json
import time
import uuid
from abc import abstractmethod
from typing import Any, Callable, Dict, Generic, Optional, TypeVar

from domain.base.exceptions import DomainException, EntityNotFoundError, ValidationError
from infrastructure.handlers.base.base_handler import BaseHandler
from infrastructure.resilience import retry

T = TypeVar("T")  # Request type
R = TypeVar("R")  # Response type


class RequestContext:
    """Request context for storing request-specific data."""

    def __init__(self) -> None:
        """Initialize request context."""
        self.correlation_id = str(uuid.uuid4())
        self.start_time = time.time()
        self.metadata: Dict[str, Any] = {}


class BaseAPIHandler(BaseHandler, Generic[T, R]):
    """
    Base class for API handlers.

    This class provides common functionality for API handlers,
    including middleware, validation, and error handling.
    """

    def __init__(self, logger=None, metrics=None) -> None:
        """
        Initialize the API handler.

        Args:
            logger: Optional logger instance
            metrics: Optional metrics collector
        """
        super().__init__(logger, metrics)

    @abstractmethod
    def handle(self, request: T) -> R:
        """
        Handle a request.

        Args:
            request: Request to handle

        Returns:
            Response
        """

    def validate(self, request: T) -> None:
        """
        Validate a request.

        Args:
            request: Request to validate

        Raises:
            ValidationError: If validation fails
        """
        # Default implementation does nothing

    def apply_middleware(
        self, func: Callable[[T], R], service_name: Optional[str] = None
    ) -> Callable[[T], Dict[str, Any]]:
        """
        Apply middleware in standardized order.

        This method applies middleware in the following order:
        1. Logging middleware
        2. Circuit breaker middleware (if service_name is provided)
        3. Error handling middleware

        Args:
            func: Function to apply middleware to
            service_name: Optional service name for circuit breaker

        Returns:
            Function with middleware applied
        """
        # Apply logging middleware
        wrapped_func = self.with_logging_middleware(func)

        # Apply circuit breaker middleware if service_name is provided
        if service_name:
            wrapped_func = self.with_retry_middleware(
                service_name=service_name, failure_threshold=3, reset_timeout=30
            )(wrapped_func)

        # Apply error handling middleware
        wrapped_func = self.with_error_handling_middleware(wrapped_func)

        return wrapped_func

    def with_logging_middleware(self, func: Callable[[T], R]) -> Callable[[T], R]:
        """
        Log requests and collect metrics.

        Args:
            func: Function to decorate

        Returns:
            Decorated function
        """

        def wrapper(request: T) -> R:
            """Wrapper function for logging middleware."""
            context = RequestContext()

            # Set correlation ID for request
            correlation_id = getattr(request, "correlation_id", context.correlation_id)

            try:
                # Log request
                self.logger.info(
                    "Processing request: %s", func.__name__,
                    extra={"correlation_id": correlation_id, "request": request},
                )

                # Record request metric
                if self.metrics:
                    self.metrics.increment_counter("requests_total")
                    self.metrics.increment_counter(f"{func.__name__}_requests_total")

                # Execute request handler
                result = func(request)

                # Record success metrics
                if self.metrics:
                    duration = time.time() - context.start_time
                    self.metrics.record_success(
                        func.__name__,
                        duration,
                        {"correlation_id": correlation_id, "duration": duration},
                    )

                # Log success
                self.logger.info(
                    "Request completed: %s", func.__name__,
                    extra={
                        "correlation_id": correlation_id,
                        "duration": time.time() - context.start_time,
                    },
                )

                return result

            except Exception as e:
                # Record error metrics
                if self.metrics:
                    self.metrics.increment_counter("requests_failed_total")
                    self.metrics.increment_counter(f"{func.__name__}_errors_total")
                    self.metrics.record_error(
                        func.__name__,
                        time.time() - context.start_time,
                        {
                            "correlation_id": correlation_id,
                            "error": str(e),
                            "error_type": e.__class__.__name__,
                        },
                    )

                # Log error with stack trace
                self.logger.error(
                    "Request failed: %s", func.__name__,
                    exc_info=True,
                    extra={
                        "correlation_id": correlation_id,
                        "error": str(e),
                        "error_type": e.__class__.__name__,
                    },
                )

                # Re-raise error
                raise

        return wrapper

    def with_error_handling_middleware(
        self, func: Callable[[T], R]
    ) -> Callable[[T], Dict[str, Any]]:
        """
        Provide standardized error handling.

        This decorator ensures that all errors are handled consistently and
        converted to a standardized API response format.

        Args:
            func: Function to decorate

        Returns:
            Decorated function that returns a standardized error response
        """

        def wrapper(request: T) -> Dict[str, Any]:
            """Wrapper function for error handling middleware."""
            try:
                result = func(request)

                # Convert result to dictionary if needed
                if hasattr(result, "to_dict") and callable(result.to_dict):
                    return result.to_dict()
                elif isinstance(result, dict):
                    return result
                else:
                    # Try to convert to JSON and back to ensure it's serializable
                    return json.loads(json.dumps(result))

            except ValueError as e:
                # Handle ValueError specifically for better error messages
                error_message = str(e)
                self.logger.error(
                    "Validation error: %s", error_message, extra={"error": error_message}
                )
                return {
                    "error": "ValidationError",
                    "message": error_message,
                    "details": {
                        "error_type": "ValueError",
                        "error_message": error_message,
                    },
                }

            except DomainException as e:
                # Handle all application-specific errors (DomainException and
                # subclasses)
                error_dict = (
                    e.to_dict()
                    if hasattr(e, "to_dict")
                    else {"code": e.__class__.__name__, "message": str(e)}
                )

                # Add correlation ID if available from the request context
                correlation_id = getattr(request, "correlation_id", None)
                if correlation_id:
                    error_dict["correlation_id"] = correlation_id

                # Log the error with appropriate level based on error type
                if isinstance(e, ValidationError):
                    self.logger.warning(
                        "Validation error: %s", str(e),
                        extra={"error_details": error_dict},
                    )
                elif isinstance(e, EntityNotFoundError):
                    self.logger.info(
                        "Not found error: %s", str(e),
                        extra={"error_details": error_dict},
                    )
                else:
                    self.logger.error(
                        "Application error: %s", str(e),
                        extra={"error_details": error_dict},
                    )

                # Return standardized error response
                return {
                    "error": e.__class__.__name__,
                    "message": str(e),
                    "details": error_dict,
                }

            except Exception as e:
                # Handle unexpected errors
                self.logger.error("Unexpected error", exc_info=True,
                                  extra={"error": str(e)})

                # Return standardized error response for unexpected errors
                return {
                    "error": "InternalError",
                    "message": "An unexpected error occurred",
                    "details": {
                        "error_type": e.__class__.__name__,
                        "error_message": str(e),
                    },
                }

        return wrapper

    def with_retry_middleware(
        self, service_name: str = "api", max_attempts: int = 3, base_delay: float = 1.0
    ) -> Callable[[Callable[[T], R]], Callable[[T], R]]:
        """
        Apply retry pattern to API calls.

        This decorator provides resilience for API calls by retrying failed operations
        with exponential backoff.

        Args:
            service_name: Name of the service being protected
            max_attempts: Maximum number of retry attempts
            base_delay: Base delay between retries

        Returns:
            Decorator function
        """

        def decorator(func: Callable[[T], R]) -> Callable[[T], R]:
            """Apply retry decorator to function."""

            @retry(
                strategy="exponential",
                max_attempts=max_attempts,
                base_delay=base_delay,
                service=service_name,
            )
            def wrapper(request: T) -> R:
                """Wrapper function for retry middleware."""
                return func(request)

            return wrapper

        return decorator

    def with_input_validation(
        self, schema: Dict[str, Any], func: Callable[[T], R]
    ) -> Callable[[T], R]:
        """
        Validate input.

        Args:
            schema: JSON Schema for input validation
            func: Function to decorate

        Returns:
            Decorated function
        """

        def wrapper(request: T) -> R:
            """Wrapper function for validation middleware."""
            # Import jsonschema directly - it's a required dependency
            from jsonschema import ValidationError as JsonSchemaValidationError
            from jsonschema import validate

            try:
                # Validate input against schema
                validate(instance=request, schema=schema)

                return func(request)

            except JsonSchemaValidationError as e:
                # Convert to domain validation error
                from domain.base.exceptions import ValidationError

                raise ValidationError(
                    domain="API",
                    message="Invalid input data",
                    field=".".join(str(p) for p in e.path) if e.path else None,
                    details={
                        "error": str(e),
                        "path": list(e.path),
                        "schema_path": list(e.schema_path),
                    },
                )

        return wrapper
