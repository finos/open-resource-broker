"""API handler for requesting machines."""

import time
from typing import Optional

from api.models import RequestMachinesModel
from api.validation import ValidationException
from application.base.infrastructure_handlers import BaseAPIHandler
from application.dto.commands import CreateRequestCommand
from application.request.dto import RequestMachinesResponse
from domain.base.dependency_injection import injectable
from domain.base.ports import ErrorHandlingPort, LoggingPort
from infrastructure.di.buses import CommandBus, QueryBus
from infrastructure.error.decorators import handle_interface_exceptions
from monitoring.metrics import MetricsCollector


@injectable
class RequestMachinesRESTHandler(BaseAPIHandler[RequestMachinesModel, RequestMachinesResponse]):
    """API handler for requesting machines."""

    def __init__(
        self,
        query_bus: QueryBus,
        command_bus: CommandBus,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        metrics: Optional[MetricsCollector] = None,
    ) -> None:
        """
        Initialize handler with pure CQRS dependencies.

        Args:
            query_bus: Query bus for CQRS queries
            command_bus: Command bus for CQRS commands
            logger: Logging port for operation logging
            error_handler: Error handling port for exception management
            metrics: Optional metrics collector
        """
        super().__init__(logger, error_handler)
        self._query_bus = query_bus
        self._command_bus = command_bus
        self._metrics_collector = metrics

    async def validate_api_request(self, request: RequestMachinesModel, context) -> None:
        """
        Validate API request for requesting machines.

        Args:
            request: Machine request model
            context: Request context
        """
        try:
            # Basic validation for required fields
            if not request.template_id:
                raise ValidationException("template_id is required")

            if not request.machine_count or request.machine_count <= 0:
                raise ValidationException("machine_count must be greater than 0")

        except ValidationException as e:
            if self.logger:
                self.logger.warning(
                    "Request validation failed: %s - Correlation ID: %s",
                    str(e),
                    context.correlation_id,
                )
            raise

    @handle_interface_exceptions(context="request_machines", interface_type="api")
    async def execute_api_request(
        self, request: RequestMachinesModel, context
    ) -> RequestMachinesResponse:
        """
        Execute the core API logic for requesting machines.

        Args:
            request: Validated machine request
            context: Request context

        Returns:
            Request machines response
        """
        if self.logger:
            self.logger.info(
                "Processing request machines - Template: %s, Count: %s - Correlation ID: %s",
                request.template_id,
                request.machine_count,
                context.correlation_id,
            )

        try:
            # Generate prefixed request ID using centralized utility
            from api.utils.request_id_generator import generate_request_id
            from domain.request.value_objects import RequestType

            # This is a machine request, so it's always ACQUIRE type
            request_id = generate_request_id(RequestType.ACQUIRE)

            # Create CQRS command
            command = CreateRequestCommand(
                request_id=request_id,
                template_id=request.template_id,
                requested_count=request.machine_count,
                metadata=getattr(request, "metadata", {}),
            )

            # Execute command through CQRS command bus
            result_request_id = await self._command_bus.execute(command)

            # Create response
            response = RequestMachinesResponse(
                request_id=result_request_id,
                metadata={"correlation_id": context.correlation_id, "submitted_at": time.time()},
            )

            if self.logger:
                self.logger.info(
                    "Successfully submitted machine request: %s - Correlation ID: %s",
                    request_id,
                    context.correlation_id,
                )

            # Record metrics if available
            if self._metrics_collector:
                self._metrics_collector.record_api_success(
                    "request_machines", request.machine_count
                )

            return response

        except Exception as e:
            if self.logger:
                self.logger.error(
                    "Failed to request machines: %s - Correlation ID: %s",
                    str(e),
                    context.correlation_id,
                )

            # Record metrics if available
            if self._metrics_collector:
                self._metrics_collector.record_api_failure("request_machines", str(e))

            raise

    async def post_process_response(
        self, response: RequestMachinesResponse, context
    ) -> RequestMachinesResponse:
        """
        Post-process the request machines response.

        Args:
            response: Original response
            context: Request context

        Returns:
            Post-processed response
        """
        # Response DTOs are frozen; return as-is or build a copy with additional metadata if needed.
        return response
