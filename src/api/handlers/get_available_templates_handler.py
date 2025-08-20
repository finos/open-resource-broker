"""API handler for retrieving available templates."""

import time
from typing import Any, Dict, Optional

from application.base.infrastructure_handlers import BaseAPIHandler
from application.dto.queries import ListTemplatesQuery
from domain.base.dependency_injection import injectable
from domain.base.ports import ErrorHandlingPort, LoggingPort
from domain.base.ports.scheduler_port import SchedulerPort
from infrastructure.di.buses import CommandBus, QueryBus

# Exception handling infrastructure
from infrastructure.error.decorators import handle_interface_exceptions
from monitoring.metrics import MetricsCollector


@injectable
class GetAvailableTemplatesRESTHandler(BaseAPIHandler[Dict[str, Any], Dict[str, Any]]):
    """API handler for retrieving available templates - CQRS-aligned implementation."""

    def __init__(
        self,
        query_bus: QueryBus,
        command_bus: CommandBus,
        scheduler_strategy: SchedulerPort,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        metrics: Optional[MetricsCollector] = None,
    ) -> None:
        """
        Initialize handler with injected CQRS dependencies.

        Args:
            query_bus: Query bus for CQRS queries (injected)
            command_bus: Command bus for CQRS commands (injected)
            scheduler_strategy: Scheduler strategy for field mapping (injected)
            logger: Logging port for operation logging
            error_handler: Error handling port for exception management
            metrics: Optional metrics collector

        Note:
            Now uses scheduler strategy instead of format service.
            All dependencies are explicitly provided via constructor.
            Follows BaseAPIHandler pattern for architectural consistency.
        """
        super().__init__(logger, error_handler)
        self._query_bus = query_bus
        self._command_bus = command_bus
        self._scheduler_strategy = scheduler_strategy
        self._metrics = metrics

    async def validate_api_request(self, request: Dict[str, Any], context) -> None:
        """
        Validate API request for getting available templates.

        Args:
            request: API request data
            context: Request context
        """
        # Basic validation - templates endpoint doesn't require specific parameters
        # but we can validate optional query parameters if present
        if "format" in request and request["format"] not in ["json", "yaml", "table"]:
            raise ValueError(f"Invalid format: {request['format']}")

    @handle_interface_exceptions
    async def execute_api_request(self, request: Dict[str, Any], context) -> Dict[str, Any]:
        """
        Execute the core API logic for retrieving available templates.

        Args:
            request: Validated API request
            context: Request context

        Returns:
            Dictionary with templates and metadata
        """
        if self.logger:
            self.logger.info(
                "Processing get available templates request - Correlation ID: %s",
                context.correlation_id,
            )

        try:
            # Create CQRS query
            query = ListTemplatesQuery()

            # Execute query through CQRS query bus
            templates = await self._query_bus.execute(query)

            # Use scheduler strategy for format conversion - SINGLE MAPPING POINT
            formatted_response = self._scheduler_strategy.format_templates_response(templates)

            # Add correlation ID and other metadata
            if isinstance(formatted_response, dict):
                formatted_response["correlation_id"] = context.correlation_id
                formatted_response["count"] = len(templates)

            if self.logger:
                self.logger.info(
                    "Successfully retrieved %s templates - Correlation ID: %s",
                    len(templates),
                    context.correlation_id,
                )

            # Record metrics if available
            if self._metrics:
                self._metrics.record_api_success("get_available_templates", len(templates))

            return formatted_response

        except Exception as e:
            if self.logger:
                self.logger.error(
                    "Failed to retrieve templates: %s - Correlation ID: %s",
                    str(e),
                    context.correlation_id,
                )

            # Record metrics if available
            if self._metrics:
                self._metrics.record_api_failure("get_available_templates", str(e))

            raise

    async def post_process_response(self, response: Dict[str, Any], context) -> Dict[str, Any]:
        """
        Post-process the template list response.

        Args:
            response: Original response
            context: Request context

        Returns:
            Post-processed response
        """
        # Add any additional metadata or formatting
        if isinstance(response, dict):
            response["processed_at"] = context.start_time
            response["processing_duration"] = time.time() - context.start_time

        return response
