"""API handler for requesting machines."""

from typing import Any, Optional, cast

from orb.api.models import RequestMachinesModel
from orb.api.validation import ValidationException
from orb.application.base.infrastructure_handlers import BaseAsyncAPIHandler as BaseAPIHandler
from orb.application.dto.commands import CreateRequestCommand
from orb.domain.base.configuration_service import DomainConfigurationService
from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import ErrorHandlingPort, LoggingPort
from orb.domain.base.ports.scheduler_port import SchedulerPort
from orb.domain.constants import REQUEST_ID_PREFIX_ACQUIRE
from orb.infrastructure.di.buses import CommandBus, QueryBus
from orb.infrastructure.error.decorators import handle_interface_exceptions
from orb.monitoring.metrics import MetricsCollector


@injectable
class RequestMachinesRESTHandler(BaseAPIHandler[RequestMachinesModel, dict[str, Any]]):
    """API handler for requesting machines."""

    def __init__(
        self,
        query_bus: QueryBus,
        command_bus: CommandBus,
        scheduler_strategy: SchedulerPort,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        metrics: Optional[MetricsCollector] = None,
        domain_config_service: Optional[DomainConfigurationService] = None,
    ) -> None:
        """
        Initialize handler with pure CQRS dependencies.

        Args:
            query_bus: Query bus for CQRS queries
            command_bus: Command bus for CQRS commands
            scheduler_strategy: Scheduler strategy for response formatting
            logger: Logging port for operation logging
            error_handler: Error handling port for exception management
            metrics: Optional metrics collector
            domain_config_service: Domain configuration service for naming config
        """
        super().__init__(logger, error_handler)
        self._query_bus = query_bus
        self._command_bus = command_bus
        self._scheduler_strategy = scheduler_strategy
        self._metrics_collector = metrics
        self._domain_config = domain_config_service

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
    ) -> dict[str, Any]:
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
            # Generate prefixed request ID using domain layer
            from orb.domain.request.request_identifiers import RequestId
            from orb.domain.request.value_objects import RequestType

            prefix = REQUEST_ID_PREFIX_ACQUIRE
            if self._domain_config:
                prefix = self._domain_config.get_acquire_request_prefix()
            request_id = str(RequestId.generate(RequestType.ACQUIRE, prefix=prefix))

            # Create CQRS command
            command = CreateRequestCommand(
                request_id=request_id,
                template_id=request.template_id,
                requested_count=request.machine_count,
                metadata=getattr(request, "metadata", {}),
            )

            # Execute command through CQRS command bus
            await self._command_bus.execute(cast(Any, command))

            if self.logger:
                self.logger.info(
                    "Successfully submitted machine request: %s - Correlation ID: %s",
                    request_id,
                    context.correlation_id,
                )

            # Record metrics if available
            if self._metrics_collector:
                cast(Any, self._metrics_collector).record_api_success(
                    "request_machines", request.machine_count
                )

            return self._scheduler_strategy.format_request_response(
                {"request_id": request_id, "status": "pending"}
            )

        except Exception as e:
            if self.logger:
                self.logger.error(
                    "Failed to request machines: %s - Correlation ID: %s",
                    str(e),
                    context.correlation_id,
                )

            # Record metrics if available
            if self._metrics_collector:
                cast(Any, self._metrics_collector).record_api_failure("request_machines", str(e))

            raise

    async def post_process_response(
        self, response: dict[str, Any], context
    ) -> dict[str, Any]:
        """
        Post-process the request machines response.

        Args:
            response: Original response
            context: Request context

        Returns:
            Post-processed response
        """
        return response
