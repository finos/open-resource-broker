"""
Base Provider Handlers for CQRS Architecture Consistency.

This module provides BaseProviderHandler that follows the same architectural
patterns as other base handlers while enabling multi-provider extensibility.
"""

import time
from abc import ABC, abstractmethod
from typing import Any, Generic, Optional, TypeVar

from orb.application.interfaces.provider_handler import ProviderHandler
from orb.domain.base.ports import ErrorHandlingPort, LoggingPort

TRequest = TypeVar("TRequest")
TResponse = TypeVar("TResponse")


class BaseProviderHandler(Generic[TRequest, TResponse], ProviderHandler[TRequest, TResponse], ABC):
    """
    Base provider handler following CQRS architecture patterns.

    This class provides the foundation for all provider handlers in the system,
    following the same architectural patterns as other base handlers:

    - Consistent error handling and logging
    - Template method pattern for request processing
    - Performance monitoring and metrics
    - Dependency injection support
    - Professional exception handling
    - Multi-provider extensibility

    Architecture Alignment:
    - ProviderHandler (interface) → BaseProviderHandler (implementation)
    - Same pattern as CommandHandler → BaseCommandHandler
    - Same pattern as QueryHandler → BaseQueryHandler
    - Same pattern as EventHandler → BaseEventHandler
    - Same pattern as InfrastructureHandler → BaseInfrastructureHandler
    """

    def __init__(
        self,
        provider_type: str,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
    ) -> None:
        """
        Initialize base provider handler.

        Args:
            provider_type: Type of cloud provider (e.g., 'aws', 'provider1', 'provider2')
            logger: Logging port for operation logging
            error_handler: Error handling port for exception management
        """
        self.provider_type = provider_type
        self.logger = logger
        self.error_handler = error_handler
        self._metrics: dict[str, Any] = {}

    async def handle(self, request: TRequest, context: Optional[object] = None) -> TResponse:  # type: ignore[override]
        """
        Handle provider request with monitoring and error management.

        Template method that provides consistent request handling
        across all provider handlers, following the same pattern
        as other base handlers in the CQRS system.
        """
        start_time = time.time()
        request_type = request.__class__.__name__
        correlation_id = f"{self.provider_type}-{int(time.time())}"

        try:
            # Log request processing start
            if self.logger:
                self.logger.info(
                    "Processing %s provider request: %s [%s]",
                    self.provider_type,
                    request_type,
                    correlation_id,
                )

            # Validate request
            await self.validate_provider_request(request)

            # Execute request processing
            response = await self.execute_provider_request(request)

            # Record success metrics
            duration = time.time() - start_time
            self._record_success_metrics(request_type, duration)

            if self.logger:
                self.logger.info(
                    "%s provider request processed successfully: %s [%s] (%.3fs)",
                    self.provider_type.upper(),
                    request_type,
                    correlation_id,
                    duration,
                )

            return response

        except Exception as e:
            # Record failure metrics
            duration = time.time() - start_time
            self._record_failure_metrics(request_type, duration, e)

            # Handle error through error handler
            if self.error_handler:
                await self.error_handler.handle_error(
                    e,
                    {
                        "provider_type": self.provider_type,
                        "request_type": request_type,
                        "correlation_id": correlation_id,
                        "duration": duration,
                    },
                )

            if self.logger:
                self.logger.error(
                    "%s provider request processing failed: %s [%s] - %s",
                    self.provider_type.upper(),
                    request_type,
                    correlation_id,
                    str(e),
                )

            # Re-raise for upstream handling
            raise

    async def validate_provider_request(self, request: TRequest) -> None:
        """
        Validate provider request before processing.

        Override this method to implement provider-specific validation.
        Default implementation performs basic validation.

        Args:
            request: Request to validate

        Raises:
            ValidationError: If request is invalid
        """
        if not request:
            raise ValueError("Request cannot be None")

    @abstractmethod
    async def execute_provider_request(self, request: TRequest) -> TResponse:
        """
        Execute provider request processing logic.

        This is the core method that concrete provider handlers must implement.
        It contains the specific business logic for handling the provider request.

        Args:
            request: Request to process

        Returns:
            Response from processing the request

        Raises:
            Any exception that occurs during request processing
        """

    def _record_success_metrics(self, request_type: str, duration: float) -> None:
        """Record success metrics for monitoring."""
        key = f"{self.provider_type}_{request_type}"
        if key not in self._metrics:
            self._metrics[key] = {
                "success_count": 0,
                "failure_count": 0,
                "total_duration": 0.0,
                "avg_duration": 0.0,
            }

        metrics = self._metrics[key]
        metrics["success_count"] += 1
        metrics["total_duration"] += duration
        total_count = metrics["success_count"] + metrics["failure_count"]
        metrics["avg_duration"] = (
            metrics["total_duration"] / total_count if total_count > 0 else 0.0
        )

    def _record_failure_metrics(self, request_type: str, duration: float, error: Exception) -> None:
        """Record failure metrics for monitoring."""
        key = f"{self.provider_type}_{request_type}"
        if key not in self._metrics:
            self._metrics[key] = {
                "success_count": 0,
                "failure_count": 0,
                "total_duration": 0.0,
                "avg_duration": 0.0,
                "last_error": None,
            }

        metrics = self._metrics[key]
        metrics["failure_count"] += 1
        metrics["total_duration"] += duration
        metrics["last_error"] = str(error)
        total_count = metrics["success_count"] + metrics["failure_count"]
        metrics["avg_duration"] = (
            metrics["total_duration"] / total_count if total_count > 0 else 0.0
        )

    def get_metrics(self) -> dict[str, Any]:
        """Get handler performance metrics."""
        return self._metrics.copy()
