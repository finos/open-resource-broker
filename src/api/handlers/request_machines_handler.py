"""API handler for requesting machines."""

import time
import uuid
from typing import TYPE_CHECKING, Optional

from api.models import RequestMachinesModel
from api.validation import RequestValidator, ValidationException
from application.base.infrastructure_handlers import BaseAPIHandler
from application.dto.commands import CreateRequestCommand
from application.request.dto import RequestMachinesResponse
from domain.base.dependency_injection import injectable
from domain.base.ports import ErrorHandlingPort, LoggingPort
from infrastructure.error.decorators import handle_interface_exceptions
from monitoring.metrics import MetricsCollector

if TYPE_CHECKING:
    from infrastructure.di.buses import CommandBus, QueryBus


@injectable
class RequestMachinesRESTHandler(BaseAPIHandler[RequestMachinesModel, RequestMachinesResponse]):
    """API handler for requesting machines."""

    def __init__(
        self,
        query_bus: "QueryBus",
        command_bus: "CommandBus",
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        metrics: Optional[MetricsCollector] = None,
    ):
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
        self._metrics = metrics
        self._validator = RequestValidator()

    async def validate_api_request(self, request: RequestMachinesModel, context) -> None:
        """
        Validate API request for requesting machines.

        Args:
            request: Machine request model
            context: Request context
        """
        try:
            # Validate using the request validator
            self._validator.validate_request_machines(request)

            # Additional validation
            if not request.template_id:
                raise ValidationException("template_id is required")

            if not request.max_number or request.max_number <= 0:
                raise ValidationException("max_number must be greater than 0")

        except ValidationException as e:
            if self.logger:
                self.logger.warning(
                    f"Request validation failed: {str(e)} - Correlation ID: {context.correlation_id}"
                )
            raise

    @handle_interface_exceptions
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
                f"Processing request machines - Template: {request.template_id}, "
                f"Count: {request.max_number} - Correlation ID: {context.correlation_id}"
            )

        try:
            # Generate request ID
            request_id = str(uuid.uuid4())

            # Create CQRS command
            command = CreateRequestCommand(
                request_id=request_id,
                template_id=request.template_id,
                max_number=request.max_number,
                priority=getattr(request, "priority", "normal"),
                metadata=getattr(request, "metadata", {}),
            )

            # Execute command through CQRS command bus
            await self._command_bus.execute(command)

            # Create response
            response = RequestMachinesResponse(
                request_id=request_id,
                template_id=request.template_id,
                requested_count=request.max_number,
                status="submitted",
                correlation_id=context.correlation_id,
                submitted_at=time.time(),
            )

            if self.logger:
                self.logger.info(
                    f"Successfully submitted machine request: {request_id} - Correlation ID: {context.correlation_id}"
                )

            # Record metrics if available
            if self._metrics:
                self._metrics.record_api_success("request_machines", request.max_number)

            return response

        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"Failed to request machines: {str(e)} - Correlation ID: {context.correlation_id}"
                )

            # Record metrics if available
            if self._metrics:
                self._metrics.record_api_failure("request_machines", str(e))

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
        # Add processing metadata
        response.processed_at = context.start_time
        response.processing_duration = time.time() - context.start_time

        return response
