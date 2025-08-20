"""
Base Provider Handlers for CQRS Architecture Consistency.

This module provides BaseProviderHandler that follows the same architectural
patterns as other base handlers while enabling multi-provider extensibility.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Generic, Optional, TypeVar

from application.interfaces.provider_handler import ProviderHandler
from domain.base.ports import ErrorHandlingPort, LoggingPort

TRequest = TypeVar("TRequest")
TResponse = TypeVar("TResponse")


class ProviderContext:
    """Context for provider operations."""

    def __init__(self, provider_type: str, region: str = None) -> None:
        """Initialize provider context."""
        self.provider_type = provider_type
        self.region = region
        self.correlation_id = f"{provider_type}-{int(time.time())}"
        self.start_time = time.time()
        self.metadata: Dict[str, Any] = {}


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
        self._metrics: Dict[str, Any] = {}

    async def handle(
        self, request: TRequest, context: Optional[ProviderContext] = None
    ) -> TResponse:
        """
        Handle provider request with monitoring and error management.

        Template method that provides consistent request handling
        across all provider handlers, following the same pattern
        as other base handlers in the CQRS system.
        """
        if not context:
            context = ProviderContext(self.provider_type)

        request_type = request.__class__.__name__

        try:
            # Log request processing start
            if self.logger:
                self.logger.info(
                    "Processing %s provider request: %s", self.provider_type, request_type
                )

            # Validate request
            await self.validate_provider_request(request, context)

            # Execute request processing
            response = await self.execute_provider_request(request, context)

            # Record success metrics
            duration = time.time() - context.start_time
            self._record_success_metrics(request_type, duration)

            if self.logger:
                self.logger.info(
                    "%s provider request processed successfully: %s (%.3fs)",
                    self.provider_type.upper(),
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
                        "provider_type": self.provider_type,
                        "request_type": request_type,
                        "correlation_id": context.correlation_id,
                        "duration": duration,
                        "context": context.metadata,
                    },
                )

            if self.logger:
                self.logger.error(
                    "%s provider request processing failed: %s - %s",
                    self.provider_type.upper(),
                    request_type,
                    str(e),
                )

            # Re-raise for upstream handling
            raise

    async def validate_provider_request(self, request: TRequest, context: ProviderContext) -> None:
        """
        Validate provider request before processing.

        Override this method to implement provider-specific validation.
        Default implementation performs basic validation.

        Args:
            request: Request to validate
            context: Provider context

        Raises:
            ValidationError: If request is invalid
        """
        if not request:
            raise ValueError("Request cannot be None")

    @abstractmethod
    async def execute_provider_request(
        self, request: TRequest, context: ProviderContext
    ) -> TResponse:
        """
        Execute provider request processing logic.

        This is the core method that concrete provider handlers must implement.
        It contains the specific business logic for handling the provider request.

        Args:
            request: Request to process
            context: Provider context with correlation ID and metadata

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

    def get_metrics(self) -> Dict[str, Any]:
        """Get handler performance metrics."""
        return self._metrics.copy()


class BaseAWSHandler(BaseProviderHandler[TRequest, TResponse]):
    """
    Base AWS handler specialized for AWS cloud provider operations.

    This handler extends BaseProviderHandler while providing AWS-specific
    functionality like AWS client management, retry logic, and error handling.
    """

    def __init__(
        self,
        aws_client,  # Type hint avoided to prevent circular imports
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        region: str = None,
    ) -> None:
        """Initialize base AWS handler."""
        super().__init__("aws", logger, error_handler)
        self.aws_client = aws_client
        self.region = region or "us-east-1"
        self.max_retries = 3
        self.base_delay = 1  # seconds
        self.max_delay = 10  # seconds

    async def validate_provider_request(self, request: TRequest, context: ProviderContext) -> None:
        """
        Validate AWS request with additional AWS-specific checks.

        Args:
            request: AWS request to validate
            context: Provider context
        """
        await super().validate_provider_request(request, context)

        # Add AWS-specific validation
        await self.validate_aws_request(request, context)

    async def validate_aws_request(self, request: TRequest, context: ProviderContext) -> None:
        """
        Validate AWS-specific request properties.

        Override this method to implement AWS-specific validation.

        Args:
            request: AWS request to validate
            context: Provider context
        """

    async def execute_provider_request(
        self, request: TRequest, context: ProviderContext
    ) -> TResponse:
        """
        Execute AWS request with retry logic and error handling.

        Args:
            request: AWS request to process
            context: Provider context

        Returns:
            AWS response
        """
        # Execute with AWS-specific retry logic
        return await self.execute_with_retry(request, context)

    async def execute_with_retry(self, request: TRequest, context: ProviderContext) -> TResponse:
        """
        Execute AWS request with exponential backoff retry logic.

        Args:
            request: AWS request to process
            context: Provider context

        Returns:
            AWS response
        """
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                return await self.execute_aws_request(request, context)

            except Exception as e:
                last_exception = e

                # Check if we should retry
                if attempt < self.max_retries and self.should_retry(e):
                    delay = min(self.base_delay * (2**attempt), self.max_delay)

                    if self.logger:
                        self.logger.warning(
                            "AWS request failed (attempt %s/%s), retrying in %ss: %s",
                            attempt + 1,
                            self.max_retries + 1,
                            delay,
                            str(e),
                        )

                    await asyncio.sleep(delay)
                    continue
                else:
                    break

        # All retries exhausted, raise the last exception
        raise last_exception

    @abstractmethod
    async def execute_aws_request(self, request: TRequest, context: ProviderContext) -> TResponse:
        """
        Execute core AWS request logic.

        This is the core method that concrete AWS handlers must implement.

        Args:
            request: AWS request to process
            context: Provider context

        Returns:
            AWS response
        """

    def should_retry(self, exception: Exception) -> bool:
        """
        Determine if an exception should trigger a retry.

        Args:
            exception: Exception that occurred

        Returns:
            True if should retry, False otherwise
        """
        # AWS-specific retry logic
        if hasattr(exception, "response"):
            error_code = exception.response.get("Error", {}).get("Code", "")

            # Retry on throttling and temporary errors
            retry_codes = [
                "Throttling",
                "ThrottlingException",
                "RequestLimitExceeded",
                "ServiceUnavailable",
                "InternalError",
                "InternalFailure",
            ]

            return error_code in retry_codes

        return False
