"""API handler for returning machines."""

import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from application.base.infrastructure_handlers import BaseAPIHandler, RequestContext
from application.dto.commands import CreateReturnRequestCommand
from application.dto.responses import (
    CleanupResourcesResponse,
    RequestReturnMachinesResponse,
)
from domain.base.dependency_injection import injectable
from domain.base.ports import ErrorHandlingPort, LoggingPort
from domain.base.ports.scheduler_port import SchedulerPort

# Exception handling infrastructure
from infrastructure.error.decorators import handle_interface_exceptions
from monitoring.metrics import MetricsCollector


@injectable
class RequestReturnMachinesRESTHandler(
    BaseAPIHandler[Dict[str, Any], RequestReturnMachinesResponse]
):
    """API handler for returning machines."""

    def __init__(
        self,
        query_bus: "QueryBus",
        command_bus: "CommandBus",
        scheduler_strategy: SchedulerPort,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        metrics: Optional[MetricsCollector] = None,
    ) -> None:
        """
        Initialize handler with CQRS dependencies.

        Args:
            query_bus: Query bus for CQRS queries
            command_bus: Command bus for CQRS commands
            scheduler_strategy: Scheduler strategy for field mapping
            logger: Logging port for operation logging
            error_handler: Error handling port for exception management
            metrics: Optional metrics collector
        """
        # Initialize with required dependencies
        super().__init__(logger, error_handler)
        self._query_bus = query_bus
        self._command_bus = command_bus
        self._scheduler_strategy = scheduler_strategy
        self._metrics = metrics

    async def validate_api_request(self, request: Dict[str, Any], context: RequestContext) -> None:
        """
        Validate API request for returning machines.

        Args:
            request: API request data
            context: Request context
        """
        # Extract parameters from request
        input_data = request.get("input_data")
        all_flag = request.get("all_flag", False)
        clean = request.get("clean", False)

        # Skip validation for cleanup or all machines
        if clean or all_flag:
            return

        # Validate input data
        if not input_data or "machines" not in input_data:
            raise ValueError("Input must include 'machines' key")

        # Validate machine data
        machines_data = input_data.get("machines", [])
        if not isinstance(machines_data, list):
            raise ValueError("'machines' must be a list")

        # Store extracted machine IDs in context for later use
        machine_ids = []
        for machine in machines_data:
            if not isinstance(machine, dict):
                raise ValueError("Each machine entry must be a dictionary")

            machine_id = machine.get("machineId")
            if not machine_id:
                continue

            if not isinstance(machine_id, str):
                raise ValueError(f"Invalid machine ID format: {machine_id}")

            machine_ids.append(machine_id)

        context.metadata["machine_ids"] = machine_ids

    @handle_interface_exceptions(context="request_return_machines_api", interface_type="api")
    async def execute_api_request(
        self, request: Dict[str, Any], context: RequestContext
    ) -> RequestReturnMachinesResponse:
        """
        Execute the core API logic for returning machines.

        Args:
            request: Validated API request
            context: Request context

        Returns:
            Request return machines response
        """
        # Extract parameters from request
        input_data = request.get("input_data")
        all_flag = request.get("all_flag", False)
        clean = request.get("clean", False)
        correlation_id = context.correlation_id
        start_time = time.time() if self._metrics else None

        try:
            # Clean up all resources
            if clean:
                if self.logger:
                    self.logger.info(
                        "Cleaning up all resources",
                        extra={"correlation_id": correlation_id},
                    )

                # Create response DTO
                return CleanupResourcesResponse(metadata={"correlation_id": correlation_id})

            if all_flag:
                # Create metadata for request
                metadata = {
                    "source_ip": request.get("client_ip"),
                    "user_agent": request.get("user_agent"),
                    "created_by": request.get("user_id"),
                    "correlation_id": correlation_id,
                    "all_machines": True,
                }

                # Create return request for all machines using CQRS command
                command = CreateReturnRequestCommand(
                    machine_ids=[],
                    metadata=metadata,  # Empty list indicates all machines
                )
                request_id = await self._command_bus.execute(command)

                if self.logger:
                    self.logger.info(
                        "Created return request for all machines with ID: %s",
                        request_id,
                        extra={
                            "request_id": request_id,
                            "correlation_id": correlation_id,
                        },
                    )

                # Create response DTO
                return RequestReturnMachinesResponse(
                    request_id=request_id,
                    message="Return request created for all machines",
                    metadata={
                        "correlation_id": correlation_id,
                        "timestamp": request.get("timestamp", time.time()),
                    },
                )
            else:
                # Get machine IDs from context or extract them
                machine_ids = context.metadata.get("machine_ids")
                if machine_ids is None and input_data and "machines" in input_data:
                    machine_ids = self._extract_machine_ids(input_data["machines"])

                if not machine_ids:
                    # Create response for no machines to return
                    return RequestReturnMachinesResponse(
                        request_id=None,
                        message="No machines to return",
                        metadata={"correlation_id": correlation_id, "machine_count": 0},
                    )

                # Log request
                if self.logger:
                    self.logger.info(
                        "Returning machines",
                        extra={
                            "correlation_id": correlation_id,
                            "machine_count": len(machine_ids),
                            "machine_ids": machine_ids,
                            "client_ip": request.get("client_ip"),
                        },
                    )

                # Create metadata for request
                metadata = {
                    "source_ip": request.get("client_ip"),
                    "user_agent": request.get("user_agent"),
                    "created_by": request.get("user_id"),
                    "correlation_id": correlation_id,
                }

                # Create return request using CQRS command
                command = CreateReturnRequestCommand(machine_ids=machine_ids, metadata=metadata)
                request_id = await self._command_bus.execute(command)

                # Record metrics if available
                if self._metrics:
                    self._metrics.record_success(
                        "request_return_machines",
                        start_time,
                        {
                            "machine_count": len(machine_ids),
                            "correlation_id": correlation_id,
                            "request_id": request_id,
                        },
                    )

                # Create response DTO
                return RequestReturnMachinesResponse(
                    request_id=request_id,
                    message="Delete VM success.",
                    metadata={
                        "correlation_id": correlation_id,
                        "machine_count": len(machine_ids),
                        "timestamp": request.get("timestamp", time.time()),
                    },
                )

        except Exception as e:
            # Record metrics if available
            if self._metrics:
                self._metrics.record_failure(
                    "request_return_machines",
                    start_time,
                    {"error": str(e), "correlation_id": correlation_id},
                )

            # Re-raise for error handling decorator
            raise

    async def post_process_response(
        self, response: RequestReturnMachinesResponse, context: RequestContext
    ) -> RequestReturnMachinesResponse:
        """
        Post-process the request return machines response.

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
            self._scheduler_strategy, "format_return_request_response"
        ):
            formatted_response = await self._scheduler_strategy.format_return_request_response(
                response
            )
            return formatted_response

        return response

    def _extract_machine_ids(self, machines_data: List[Dict[str, Any]]) -> List[str]:
        """
        Extract and validate machine IDs from input data.

        Args:
            machines_data: List of machine data dictionaries

        Returns:
            List of machine IDs

        Raises:
            ValueError: If machine data is invalid
        """
        machine_ids = []
        for machine in machines_data:
            if not isinstance(machine, dict):
                raise ValueError("Each machine entry must be a dictionary")

            machine_id = machine.get("machineId")
            if not machine_id:
                continue

            if not isinstance(machine_id, str):
                raise ValueError(f"Invalid machine ID format: {machine_id}")

            machine_ids.append(machine_id)

        return machine_ids


if TYPE_CHECKING:
    from infrastructure.di.buses import CommandBus, QueryBus
