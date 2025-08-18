"""
Base Infrastructure Handlers for CQRS Architecture Consistency.

This module provides BaseInfrastructureHandler that follows the same architectural
patterns as BaseCommandHandler, BaseQueryHandler, and BaseEventHandler, ensuring
consistency across all handler types in the CQRS system.
"""

import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Generic, Optional, TypeVar

from application.interfaces.infrastructure_handler import InfrastructureHandler
from domain.base.ports import ErrorHandlingPort, LoggingPort

TRequest = TypeVar("TRequest")
TResponse = TypeVar("TResponse")


class RequestContext:
    """Request context for storing request-specific data."""

    def __init__(self) -> None:
        """Initialize request context."""
        self.correlation_id = str(uuid.uuid4())
        self.start_time = time.time()
        self.metadata: Dict[str, Any] = {}


class BaseInfrastructureHandler(
    Generic[TRequest, TResponse], InfrastructureHandler[TRequest, TResponse], ABC
):
    """
    Base infrastructure handler following CQRS architecture patterns.

    This class provides the foundation for all infrastructure handlers in the system,
    following the same architectural patterns as other base handlers:

    - Consistent error handling and logging
    - Template method pattern for request processing
    - Performance monitoring and metrics
    - Dependency injection support
    - Professional exception handling

    Architecture Alignment:
    - InfrastructureHandler (interface) -> BaseInfrastructureHandler (implementation)
    - Same pattern as CommandHandler -> BaseCommandHandler
    - Same pattern as QueryHandler -> BaseQueryHandler
    - Same pattern as EventHandler -> BaseEventHandler
    """

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
    ) -> None:
        """
        Initialize base infrastructure handler.

        Args:
            logger: Logging port for operation logging
            error_handler: Error handling port for exception management
        """
        self.logger = logger
        self.error_handler = error_handler
        self._metrics: Dict[str, Any] = {}

    async def handle(self, request: TRequest) -> TResponse:
        """
        Handle infrastructure request with monitoring and error management.

        Template method that provides consistent request handling
        across all infrastructure handlers, following the same pattern
        as other base handlers in the CQRS system.
        """
        context = RequestContext()
        request_type = request.__class__.__name__

        try:
            # Log request processing start
            if self.logger:
                self.logger.info("Processing infrastructure request: %s", request_type)

            # Validate request
            await self.validate_request(request, context)

            # Execute request processing
            response = await self.execute_request(request, context)

            # Record success metrics
            duration = time.time() - context.start_time
            self._record_success_metrics(request_type, duration)

            if self.logger:
                self.logger.info(
                    "Infrastructure request processed successfully: %s (%.3fs)",
                    request_type,
                    duration,
                )

            return response

        except Exception as e:
            # Record failure metrics
            duration = time.time() - context.start_time
            self._record_failure_metrics(request_type, duration, e)

            # Handle error through error handler
            if self.error_handler:
                await self.error_handler.handle_error(
                    e,
                    {
                        "request_type": request_type,
                        "correlation_id": context.correlation_id,
                        "duration": duration,
                        "context": context.metadata,
                    },
                )

            if self.logger:
                self.logger.error(
                    "Infrastructure request processing failed: %s - %s", request_type, str(e)
                )

            # Re-raise for upstream handling
            raise

    async def validate_request(self, request: TRequest, context: RequestContext) -> None:
        """
        Validate infrastructure request before processing.

        Override this method to implement request-specific validation.
        Default implementation performs basic validation.

        Args:
            request: Request to validate
            context: Request context

        Raises:
            ValidationError: If request is invalid
        """
        if not request:
            raise ValueError("Request cannot be None") from e

    @abstractmethod
    async def execute_request(self, request: TRequest, context: RequestContext) -> TResponse:
        """
        Execute infrastructure request processing logic.

        This is the core method that concrete infrastructure handlers must implement.
        It contains the specific business logic for handling the request.

        Args:
            request: Request to process
            context: Request context with correlation ID and metadata

        Returns:
            Response from processing the request

        Raises:
            Any exception that occurs during request processing
        """

    def _record_success_metrics(self, request_type: str, duration: float) -> None:
        """Record success metrics for monitoring."""
        if request_type not in self._metrics:
            self._metrics[request_type] = {
                "success_count": 0,
                "failure_count": 0,
                "total_duration": 0.0,
                "avg_duration": 0.0,
            }

        metrics = self._metrics[request_type]
        metrics["success_count"] += 1
        metrics["total_duration"] += duration
        total_count = metrics["success_count"] + metrics["failure_count"]
        metrics["avg_duration"] = (
            metrics["total_duration"] / total_count if total_count > 0 else 0.0
        )

    def _record_failure_metrics(self, request_type: str, duration: float, error: Exception) -> None:
        """Record failure metrics for monitoring."""
        if request_type not in self._metrics:
            self._metrics[request_type] = {
                "success_count": 0,
                "failure_count": 0,
                "total_duration": 0.0,
                "avg_duration": 0.0,
                "last_error": None,
            }

        metrics = self._metrics[request_type]
        metrics["failure_count"] += 1
        metrics["total_duration"] += duration
        metrics["last_error"] = str(error)
        total_count = metrics["success_count"] + metrics["failure_count"]
        metrics["avg_duration"] = (
            metrics["total_duration"] / total_count if total_count > 0 else 0.0
        )

    def get_metrics(self) -> Dict[str, Any]:
        """Get handler performance metrics."""
        return self._metrics.copy()


class BaseAPIHandler(BaseInfrastructureHandler[TRequest, TResponse]):
    """
    Base API handler specialized for HTTP/REST API requests.

    This handler extends BaseInfrastructureHandler while providing
    API-specific functionality like middleware, validation, and response formatting.
    """

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
    ) -> None:
        """Initialize base API handler."""
        super().__init__(logger, error_handler)
        self.middleware_stack: list[Callable] = []

    async def validate_request(self, request: TRequest, context: RequestContext) -> None:
        """
        Validate API request with additional HTTP-specific checks.

        Args:
            request: API request to validate
            context: Request context
        """
        await super().validate_request(request, context)

        # Add API-specific validation
        await self.validate_api_request(request, context)

    async def validate_api_request(self, request: TRequest, context: RequestContext) -> None:
        """
        Validate API-specific request properties.

        Override this method to implement API-specific validation.

        Args:
            request: API request to validate
            context: Request context
        """

    async def execute_request(self, request: TRequest, context: RequestContext) -> TResponse:
        """
        Execute API request with middleware processing.

        Args:
            request: API request to process
            context: Request context

        Returns:
            API response
        """
        # Apply middleware stack
        processed_request = await self.apply_middleware(request, context)

        # Execute core API logic
        response = await self.execute_api_request(processed_request, context)

        # Post-process response
        return await self.post_process_response(response, context)

    @abstractmethod
    async def execute_api_request(self, request: TRequest, context: RequestContext) -> TResponse:
        """
        Execute core API request logic.

        This is the core method that concrete API handlers must implement.

        Args:
            request: Processed API request
            context: Request context

        Returns:
            API response
        """

    async def apply_middleware(self, request: TRequest, context: RequestContext) -> TRequest:
        """
        Apply middleware stack to request.

        Args:
            request: Original request
            context: Request context

        Returns:
            Processed request
        """
        processed_request = request

        for middleware in self.middleware_stack:
            processed_request = await middleware(processed_request, context)

        return processed_request

    async def post_process_response(
        self, response: TResponse, context: RequestContext
    ) -> TResponse:
        """
        Post-process API response.

        Override this method to implement response post-processing.

        Args:
            response: Original response
            context: Request context

        Returns:
            Post-processed response
        """
        return response

    def add_middleware(self, middleware: Callable) -> None:
        """
        Add middleware to the processing stack.

        Args:
            middleware: Middleware function to add
        """
        self.middleware_stack.append(middleware)
