"""API handler for getting return requests."""

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.application.base.infrastructure_handlers import BaseAPIHandler, RequestContext
from src.application.dto.responses import ReturnRequestResponse
from src.application.request.queries import ListRequestsQuery
from src.config import RequestConfig
from src.config.manager import get_config_manager
from src.domain.base.dependency_injection import injectable
from src.domain.base.ports import ErrorHandlingPort, LoggingPort
from src.domain.base.ports.scheduler_port import SchedulerPort

# Exception handling infrastructure
from src.infrastructure.error.decorators import handle_interface_exceptions
from src.monitoring.metrics import MetricsCollector

if TYPE_CHECKING:
    from src.infrastructure.di.buses import CommandBus, QueryBus


@injectable
class GetReturnRequestsRESTHandler(BaseAPIHandler[Dict[str, Any], ReturnRequestResponse]):
    """API handler for getting return requests."""

    def __init__(
        self,
        query_bus: "QueryBus",
        command_bus: "CommandBus",
        scheduler_strategy: SchedulerPort,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        metrics: Optional[MetricsCollector] = None,
        cache_duration: int = 60,
    ):  # Cache duration in seconds
        """
        Initialize handler with pure CQRS dependencies.

        Args:
            query_bus: Query bus for CQRS queries
            command_bus: Command bus for CQRS commands
            scheduler_strategy: Scheduler strategy for field mapping
            logger: Logging port for operation logging
            error_handler: Error handling port for exception management
            metrics: Optional metrics collector
            cache_duration: Cache duration in seconds
        """
        # Initialize with required dependencies
        super().__init__(logger, error_handler)
        self._query_bus = query_bus
        self._command_bus = command_bus
        self._scheduler_strategy = scheduler_strategy
        self._metrics = metrics
        self._cache_duration = cache_duration
        self._cache = {}

    async def validate_api_request(self, request: Dict[str, Any], context: RequestContext) -> None:
        """
        Validate API request for getting return requests.

        Args:
            request: API request data
            context: Request context
        """
        # Extract parameters from request
        input_data = request.get("input_data")

        # Validate filters if provided
        if input_data and "filters" in input_data:
            filters = input_data["filters"]

            # Validate time range if provided
            if "time_range" in filters:
                try:
                    start_time = datetime.fromisoformat(filters["time_range"]["start"])
                    end_time = datetime.fromisoformat(filters["time_range"]["end"])

                    if start_time > end_time:
                        raise ValueError("Start time must be before end time")

                except (ValueError, KeyError) as e:
                    raise ValueError(f"Invalid time range format: {str(e)}")

    @handle_interface_exceptions(context="get_return_requests_api", interface_type="api")
    async def execute_api_request(
        self, request: Dict[str, Any], context: RequestContext
    ) -> ReturnRequestResponse:
        """
        Execute the core API logic for getting return requests.

        Args:
            request: Validated API request
            context: Request context

        Returns:
            Return request response
        """
        # Extract parameters from request
        input_data = request.get("input_data")
        long = request.get("long", False)
        correlation_id = context.correlation_id
        start_time = time.time() if self._metrics else None

        if self.logger:
            self.logger.info(
                "Getting return requests",
                extra={
                    "correlation_id": correlation_id,
                    "long_format": long,
                    "filters": input_data,
                    "client_ip": request.get("client_ip"),
                },
            )

        try:
            # Try to get from cache first
            cache_key = self._get_cache_key(input_data, long)
            cached_result = self._get_from_cache(cache_key)
            if cached_result:
                if self.logger:
                    self.logger.debug(
                        "Returning cached result",
                        extra={"correlation_id": correlation_id},
                    )
                return ReturnRequestResponse.from_dict(cached_result)

            # Get return requests using CQRS query
            query = ListRequestsQuery(
                status="return_requested", limit=100
            )  # Filter for return requests
            return_requests = await self._query_bus.execute(query)

            # Apply filters if provided
            if input_data and "filters" in input_data:
                return_requests = self._apply_filters(return_requests, input_data["filters"])

            # Format response
            formatted_requests = []
            for req in return_requests:
                request_data = {
                    "machine": (
                        req.machines[0].name if hasattr(req, "machines") and req.machines else None
                    ),
                    "gracePeriod": await self._calculate_grace_period(req),
                    "status": (req.status.value if hasattr(req.status, "value") else req.status),
                    "requestId": str(req.request_id),
                    "createdAt": (
                        req.created_at.isoformat()
                        if hasattr(req.created_at, "isoformat")
                        else req.created_at
                    ),
                }

                if long:
                    request_data.update(
                        {
                            "machines": (
                                [m.to_dict() if hasattr(m, "to_dict") else m for m in req.machines]
                                if hasattr(req, "machines")
                                else []
                            ),
                            "metadata": (req.metadata if hasattr(req, "metadata") else {}),
                            "events": (
                                [e.to_dict() if hasattr(e, "to_dict") else e for e in req.events]
                                if hasattr(req, "events")
                                else []
                            ),
                        }
                    )

                formatted_requests.append(request_data)

            # Create response DTO
            response = ReturnRequestResponse(
                requests=formatted_requests,
                metadata={
                    "correlation_id": correlation_id,
                    "timestamp": request.get("timestamp", time.time()),
                    "request_count": len(formatted_requests),
                    "filters_applied": bool(input_data and "filters" in input_data),
                },
            )

            # Cache the result
            self._add_to_cache(cache_key, response.to_dict())

            # Record metrics if available
            if self._metrics:
                self._metrics.record_success(
                    "get_return_requests",
                    start_time,
                    {
                        "request_count": len(formatted_requests),
                        "correlation_id": correlation_id,
                    },
                )

            return response

        except Exception as e:
            # Record metrics if available
            if self._metrics:
                self._metrics.record_failure(
                    "get_return_requests",
                    start_time,
                    {"error": str(e), "correlation_id": correlation_id},
                )

            # Re-raise for error handling decorator
            raise

    async def post_process_response(
        self, response: ReturnRequestResponse, context: RequestContext
    ) -> ReturnRequestResponse:
        """
        Post-process the return request response.

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
            self._scheduler_strategy, "format_return_requests_response"
        ):
            formatted_response = await self._scheduler_strategy.format_return_requests_response(
                response
            )
            return formatted_response

        return response

    def _get_cache_key(self, input_data: Optional[Dict[str, Any]], long: bool) -> str:
        """
        Generate cache key based on input parameters.

        Args:
            input_data: Input data for filtering
            long: Whether to return detailed information

        Returns:
            Cache key
        """
        return f"return_requests_{hash(str(input_data))}_{long}"

    def _get_from_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """
        Get result from cache if valid.

        Args:
            cache_key: Cache key

        Returns:
            Cached data if valid, None otherwise
        """
        if cache_key in self._cache:
            cached_data = self._cache[cache_key]
            if (
                datetime.utcnow() - cached_data["timestamp"]
            ).total_seconds() < self._cache_duration:
                return cached_data["data"]
            else:
                del self._cache[cache_key]
        return None

    def _add_to_cache(self, cache_key: str, data: Dict[str, Any]) -> None:
        """
        Add result to cache.

        Args:
            cache_key: Cache key
            data: Data to cache
        """
        self._cache[cache_key] = {"data": data, "timestamp": datetime.utcnow()}
        self._cleanup_cache()

    def _cleanup_cache(self) -> None:
        """Clean up expired cache entries."""
        now = datetime.utcnow()
        expired_keys = [
            key
            for key, value in self._cache.items()
            if (now - value["timestamp"]).total_seconds() >= self._cache_duration
        ]
        for key in expired_keys:
            del self._cache[key]

    def _apply_filters(self, requests: List[Any], filters: Dict[str, Any]) -> List[Any]:
        """
        Apply filters to return requests.

        Args:
            requests: List of return requests
            filters: Filters to apply

        Returns:
            Filtered list of return requests
        """
        filtered_requests = requests

        if "status" in filters:
            filtered_requests = [
                r
                for r in filtered_requests
                if (
                    r.status.value
                    if hasattr(r.status, "value") and not isinstance(r.status, str)
                    else r.status
                )
                == filters["status"]
            ]

        if "machine_name" in filters:
            filtered_requests = [
                r
                for r in filtered_requests
                if hasattr(r, "machines")
                and any(m.name == filters["machine_name"] for m in r.machines)
            ]

        if "time_range" in filters:
            start_time = datetime.fromisoformat(filters["time_range"]["start"])
            end_time = datetime.fromisoformat(filters["time_range"]["end"])
            filtered_requests = [
                r for r in filtered_requests if start_time <= r.created_at <= end_time
            ]

        return filtered_requests

    async def _calculate_grace_period(self, request: Any) -> int:
        """
        Calculate grace period for return request.

        Args:
            request: Return request

        Returns:
            Grace period in seconds
        """
        if not hasattr(request, "machines") or not request.machines:
            return 0

        # Get default grace period from configuration
        config = get_config_manager().get_typed(RequestConfig)
        default_grace_period = config.default_grace_period

        # Check if machine is spot instance
        if hasattr(request, "machines") and any(
            hasattr(m, "price_type") and m.price_type == "spot" for m in request.machines
        ):
            # Spot instances get 2 minutes grace period
            return 120

        return default_grace_period
