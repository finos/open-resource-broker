"""API handler for checking request status."""

import time
from typing import TYPE_CHECKING, Any, Optional

from orb.api.models import RequestStatusModel
from orb.api.validation import RequestValidator, ValidationException
from orb.application.base.infrastructure_handlers import (
    BaseAsyncAPIHandler as BaseAPIHandler,
    RequestContext,
)
from orb.application.dto.queries import GetRequestQuery, ListActiveRequestsQuery
from orb.application.ports.scheduler_port import SchedulerPort
from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import ErrorHandlingPort, LoggingPort
from orb.domain.request.exceptions import RequestNotFoundError
from orb.domain.request.request_identifiers import RequestId
from orb.infrastructure.di.buses import CommandBus, QueryBus
from orb.infrastructure.error.decorators import handle_interface_exceptions
from orb.monitoring.metrics import MetricsCollector


@injectable
class GetRequestStatusRESTHandler(BaseAPIHandler[dict[str, Any], dict[str, Any]]):
    """API handler for checking request status."""

    def __init__(
        self,
        query_bus: QueryBus,
        command_bus: CommandBus,
        scheduler_strategy: SchedulerPort,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        metrics: Optional[MetricsCollector] = None,
        max_retries: int = 3,
    ) -> None:
        """
        Initialize handler with pure CQRS dependencies.

        Args:
            query_bus: Query bus for CQRS queries
            command_bus: Command bus for CQRS commands
            scheduler_strategy: Scheduler strategy for field mapping
            logger: Logging port for operation logging
            error_handler: Error handling port for exception management
            metrics: Optional metrics collector
            max_retries: Maximum number of retries for failed requests
        """
        # Initialize with required dependencies
        super().__init__(logger, error_handler)
        self._query_bus = query_bus
        self._command_bus = command_bus
        self._scheduler_strategy = scheduler_strategy
        self._metrics_collector = metrics
        self._max_retries = max_retries
        self.validator = RequestValidator()

    async def validate_api_request(self, request: dict[str, Any], context: RequestContext) -> None:
        """
        Validate API request for checking request status.

        Args:
            request: API request data
            context: Request context
        """
        # Extract parameters from request
        input_data = request.get("input_data")
        all_flag = request.get("all_flag", False)

        # Validate input if not using all_flag
        if not all_flag and input_data is None:
            raise ValueError("Input data is required when not using all_flag")

        # If input_data is provided, validate it
        if not all_flag and input_data is not None:
            try:
                validated_data = self.validator.validate(RequestStatusModel, input_data)
                if not validated_data.request_ids:
                    raise ValueError("No request IDs provided")

                # Store validated data in context for later use
                context.metadata["validated_data"] = validated_data

            except ValidationException as e:
                raise ValueError(f"Validation error: {e.message}")

    @handle_interface_exceptions(context="get_request_status_api", interface_type="api")
    async def execute_api_request(
        self, request: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        """
        Execute the core API logic for checking request status.

        Args:
            request: Validated API request
            context: Request context

        Returns:
            Request status response
        """
        # Extract parameters from request
        input_data = request.get("input_data")
        all_flag = request.get("all_flag", False)
        long = request.get("long", False)
        correlation_id = context.correlation_id
        start_time = time.time() if self._metrics_collector else None

        if self.logger:
            self.logger.info(
                "Getting request status",
                extra={
                    "correlation_id": correlation_id,
                    "all_flag": all_flag,
                    "long_format": long,
                    "client_ip": request.get("client_ip"),
                },
            )

        try:
            errors: list[dict[str, Any]] = []
            if all_flag:
                # Get all active requests using CQRS query
                query = ListActiveRequestsQuery()
                requests = await self._query_bus.execute(query)
                response = self._scheduler_strategy.format_request_status_response(requests)
            else:
                # Get validated data from context
                validated_data = context.metadata.get("validated_data")
                if not validated_data:
                    # Fallback validation if not done in validate_api_request
                    if input_data is None:
                        raise ValueError("Input data is required")
                    validated_data = self.validator.validate(RequestStatusModel, input_data)

                request_ids = validated_data.request_ids
                requests = []
                errors = []

                # Process each request ID
                for request_id in request_ids:
                    # Validate that request_id has proper prefix (req-/ret-)
                    if not RequestId._is_valid_format(str(request_id)):
                        raise ValueError(
                            f"Invalid request ID format: '{request_id}'. "
                            "Request IDs must start with 'req-' or 'ret-' prefix."
                        )

                    try:
                        request_data = await self._get_request_with_retry(request_id, long)

                        if self.logger:
                            self.logger.info(
                                "Retrieved status for request %s",
                                request_id,
                                extra={
                                    "request_id": request_id,
                                    "correlation_id": correlation_id,
                                    "status": (
                                        request_data.status.value
                                        if hasattr(request_data, "status")
                                        and hasattr(request_data.status, "value")
                                        and not isinstance(request_data.status, str)
                                        else (
                                            request_data.status
                                            if hasattr(request_data, "status")
                                            else request_data
                                        )
                                    ),
                                },
                            )

                        requests.append(self._normalize_request_payload(request_data, request_id))
                    except Exception as e:
                        if self.logger:
                            self.logger.error(
                                "Failed to get status for request %s",
                                request_id,
                                extra={
                                    "request_id": request_id,
                                    "correlation_id": correlation_id,
                                    "error": str(e),
                                },
                            )

                        errors.append({"request_id": request_id, "error": str(e)})

                # Create response using scheduler strategy
                response = self._scheduler_strategy.format_request_status_response(requests)

            # Record metrics if available
            if self._metrics_collector and start_time is not None:
                self._metrics_collector.record_success(
                    "get_request_status",
                    start_time,
                    {
                        "request_count": len(requests),
                        "error_count": len(errors) if "errors" in locals() else 0,
                        "correlation_id": correlation_id,
                    },
                )

            return response

        except Exception as e:
            # Record metrics if available
            if self._metrics_collector and start_time is not None:
                self._metrics_collector.record_error(
                    "get_request_status",
                    start_time,
                    {"error": str(e), "correlation_id": correlation_id},
                )

            # Re-raise for error handling decorator
            raise

    async def post_process_response(
        self, response: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        """
        Post-process the request status response.

        Args:
            response: Original response
            context: Request context

        Returns:
            Post-processed response
        """
        return response

    def _normalize_request_payload(self, request_data: Any, request_id: str) -> dict[str, Any]:
        """
        Normalize request payload for API response and fix return request type.

        Args:
            request_data: Request DTO/object or raw status
            request_id: ID used for the lookup

        Returns:
            Dictionary suitable for API response
        """
        if hasattr(request_data, "to_dict"):
            payload = request_data.to_dict()
        elif hasattr(request_data, "model_dump"):
            payload = request_data.model_dump(by_alias=True)
        elif isinstance(request_data, dict):
            payload = request_data
        else:
            payload = {"request_id": request_id, "status": request_data}

        # Ensure request_type matches ID prefix for return requests
        if RequestId.is_return_id(str(request_id)):
            payload["request_type"] = "return"

        return payload

    async def _get_request_with_retry(self, request_id: str, long: bool) -> Any:
        """
        Get request status with retry mechanism.

        Args:
            request_id: Request ID
            long: Whether to return detailed information

        Returns:
            Request object or status string

        Raises:
            Exception: If all retries fail
        """
        last_error = None
        for attempt in range(self._max_retries):
            try:
                if long:
                    # Get full request details (including machines) via request query
                    query = GetRequestQuery(request_id=request_id, long=long)
                else:
                    # Get basic request status via lightweight query
                    query = GetRequestQuery(request_id=request_id, lightweight=True)

                return await self._query_bus.execute(query)
            except RequestNotFoundError:
                # Don't retry if request not found
                raise
            except Exception as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    if self.logger:
                        self.logger.warning(
                            "Retry %s/%s for request %s",
                            attempt + 1,
                            self._max_retries,
                            request_id,
                        )
                    continue

        # If we get here, all retries failed
        if last_error:
            raise last_error
        else:
            raise Exception(f"Failed to get request status after {self._max_retries} attempts")


if TYPE_CHECKING:
    from orb.infrastructure.di.buses import CommandBus, QueryBus
