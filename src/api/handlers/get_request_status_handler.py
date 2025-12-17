"""API handler for checking request status."""

import time
from typing import TYPE_CHECKING, Any, Optional

from api.models import RequestStatusModel
from api.validation import RequestValidator, ValidationException
from application.base.infrastructure_handlers import BaseAPIHandler, RequestContext
from application.dto.queries import GetRequestQuery, GetRequestStatusQuery, ListActiveRequestsQuery
from application.request.dto import RequestStatusResponse
from domain.base.dependency_injection import injectable
from domain.base.ports import ErrorHandlingPort, LoggingPort
from domain.base.ports.scheduler_port import SchedulerPort
from domain.request.exceptions import RequestNotFoundError
from infrastructure.di.buses import CommandBus, QueryBus
from infrastructure.error.decorators import handle_interface_exceptions
from monitoring.metrics import MetricsCollector


@injectable
class GetRequestStatusRESTHandler(BaseAPIHandler[dict[str, Any], RequestStatusResponse]):
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
        self._metrics = metrics
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
    ) -> RequestStatusResponse:
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
        start_time = time.time() if self._metrics else None

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
            if all_flag:
                # Get all active requests using CQRS query
                query = ListActiveRequestsQuery()
                requests = await self._query_bus.execute(query)

                # Create response DTO
                response = RequestStatusResponse(
                    requests=[
                        req.to_dict() if hasattr(req, "to_dict") else req for req in requests
                    ],
                    metadata={
                        "correlation_id": correlation_id,
                        "timestamp": request.get("timestamp"),
                        "request_count": len(requests),
                        "error_count": 0,
                    },
                )
            else:
                # Get validated data from context
                validated_data = context.metadata.get("validated_data")
                if not validated_data:
                    # Fallback validation if not done in validate_api_request
                    validated_data = self.validator.validate(RequestStatusModel, input_data)

                request_ids = validated_data.request_ids
                requests = []
                errors = []

                # Process each request ID
                for request_id in request_ids:
                    # Validate that request_id has proper prefix (req-/ret-)
                    if not str(request_id).startswith(("req-", "ret-")):
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

                        errors.append({"requestId": request_id, "error": str(e)})

                # Create response DTO
                response = RequestStatusResponse(
                    requests=requests,
                    errors=errors if errors else None,
                    metadata={
                        "correlation_id": correlation_id,
                        "timestamp": request.get("timestamp"),
                        "request_count": len(requests),
                        "error_count": len(errors),
                    },
                )

            # Record metrics if available
            if self._metrics:
                self._metrics.record_success(
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
            if self._metrics:
                self._metrics.record_failure(
                    "get_request_status",
                    start_time,
                    {"error": str(e), "correlation_id": correlation_id},
                )

            # Re-raise for error handling decorator
            raise

    async def post_process_response(
        self, response: RequestStatusResponse, context: RequestContext
    ) -> RequestStatusResponse:
        """
        Post-process the request status response.

        Args:
            response: Original response
            context: Request context

        Returns:
            Post-processed response
        """
        # Add processing metadata
        if hasattr(response, "metadata") and response.metadata:
            response.metadata["processed_at"] = time.time()
            response.metadata["processing_duration"] = time.time() - context.start_time

            # Apply scheduler strategy for format conversion if needed
            if self._scheduler_strategy and hasattr(
                self._scheduler_strategy, "format_request_response"
            ):
                # Convert Pydantic DTO to dict before formatting
                response_payload = (
                    response.model_dump()
                    if hasattr(response, "model_dump")
                    else response.to_dict()
                    if hasattr(response, "to_dict")
                    else response
                )
            formatter = self._scheduler_strategy.format_request_response
            if callable(formatter):
                if hasattr(formatter, "__call__") and getattr(formatter, "__name__", ""):
                    # If formatter is async, await; otherwise call directly
                    try:
                        import inspect

                        if inspect.iscoroutinefunction(formatter):
                            formatted_response = await formatter(response_payload)
                        else:
                            formatted_response = formatter(response_payload)
                    except TypeError:
                        # Fallback: attempt synchronous call
                        formatted_response = formatter(response_payload)
                else:
                    formatted_response = formatter(response_payload)
                return formatted_response

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
            payload = request_data.model_dump()
        elif isinstance(request_data, dict):
            payload = request_data
        else:
            payload = {"requestId": request_id, "status": request_data}

        # Ensure request_type matches ID prefix for return requests
        if str(request_id).startswith("ret-"):
            payload["request_type"] = "return"
            payload["requestType"] = "return"

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
                    # Get basic request status via lightweight status query
                    query = GetRequestStatusQuery(request_id=request_id)

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
    from infrastructure.di.buses import CommandBus, QueryBus
