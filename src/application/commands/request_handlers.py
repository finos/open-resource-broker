"""Command handlers for request operations."""

from typing import Any, Dict

from src.application.base.handlers import BaseCommandHandler
from src.application.decorators import command_handler
from src.application.dto.commands import (
    CancelRequestCommand,
    CompleteRequestCommand,
    CreateRequestCommand,
    CreateReturnRequestCommand,
    UpdateRequestStatusCommand,
)
from src.application.services.provider_capability_service import (
    ProviderCapabilityService,
    ValidationLevel,
)
from src.application.services.provider_selection_service import ProviderSelectionService
from src.domain.base import UnitOfWorkFactory
from src.domain.base.exceptions import EntityNotFoundError
from src.domain.base.ports import (
    ContainerPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
    ProviderPort,
)
from src.domain.machine.repository import MachineRepository
from src.domain.request.repository import RequestRepository
from src.infrastructure.di.buses import QueryBus


@command_handler(CreateRequestCommand)
class CreateMachineRequestHandler(BaseCommandHandler[CreateRequestCommand, str]):
    """Handler for creating machine requests."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        query_bus: QueryBus,  # QueryBus is required for template lookup
        provider_selection_service: ProviderSelectionService,
        provider_capability_service: ProviderCapabilityService,
        provider_port: ProviderPort,
    ):
        """Initialize the instance."""
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory  # Use UoW factory pattern
        self._container = container
        self._query_bus = query_bus
        self._provider_selection_service = provider_selection_service
        self._provider_capability_service = provider_capability_service
        self._provider_context = provider_context

    async def validate_command(self, command: CreateRequestCommand) -> None:
        """Validate create request command."""
        await super().validate_command(command)
        if not command.template_id:
            raise ValueError("template_id is required")
        if not command.requested_count or command.requested_count <= 0:
            raise ValueError("requested_count must be positive")

    async def execute_command(self, command: CreateRequestCommand) -> str:
        """Handle machine request creation command."""
        self.logger.info(f"Creating machine request for template: {command.template_id}")

        # CRITICAL VALIDATION: Ensure providers are available
        if not self._provider_context.available_strategies:
            error_msg = "No provider strategies available - cannot create machine requests"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        self.logger.debug(
            f"Available provider strategies: { self._provider_context.available_strategies}"
        )

        # Initialize request variable
        request = None

        try:
            # Get template using CQRS QueryBus
            if not self._query_bus:
                raise ValueError("QueryBus is required for template lookup")

            from src.application.dto.queries import GetTemplateQuery

            template_query = GetTemplateQuery(template_id=command.template_id)
            template = await self._query_bus.execute(template_query)

            if not template:
                raise EntityNotFoundError("Template", command.template_id)

            # Select provider based on template requirements
            selection_result = self._provider_selection_service.select_provider_for_template(
                template
            )
            self.logger.info(
                f"Selected provider: { selection_result.provider_instance} ({ selection_result.selection_reason})"
            )

            # Validate template compatibility with selected provider
            validation_result = self._provider_capability_service.validate_template_requirements(
                template, selection_result.provider_instance, ValidationLevel.STRICT
            )

            if not validation_result.is_valid:
                error_msg = f"Template incompatible with provider {selection_result.provider_instance}: {'; '.join(validation_result.errors)}"
                self.logger.error(error_msg)
                raise ValueError(error_msg)

            self.logger.info(f"Template validation passed: {validation_result.supported_features}")

            # Create request aggregate with selected provider
            from src.domain.request.aggregate import Request
            from src.domain.request.value_objects import RequestType

            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id=command.template_id,
                machine_count=command.requested_count,
                provider_type=selection_result.provider_type,
                provider_instance=selection_result.provider_instance,
                metadata={
                    **command.metadata,
                    "dry_run": getattr(command, "dry_run", False),
                    "provider_selection_reason": selection_result.selection_reason,
                    "provider_confidence": selection_result.confidence,
                },
            )

            # Check if this is a dry-run request
            is_dry_run = request.metadata.get("dry_run", False)

            if is_dry_run:
                # In dry-run mode, skip actual provisioning
                self.logger.info(
                    f"Skipping actual provisioning for request { request.request_id} (dry-run mode)"
                )
                from src.domain.request.value_objects import RequestStatus

                request = request.update_status(
                    RequestStatus.COMPLETED, "Request created successfully (dry-run)"
                )
            else:
                # Execute actual provisioning using selected provider
                try:
                    provisioning_result = await self._execute_provisioning(
                        template, request, selection_result
                    )

                    # Update request with provisioning results
                    if provisioning_result.get("success"):
                        # Store resource IDs in request - ensure we get the actual list
                        resource_ids = provisioning_result.get("resource_ids", [])
                        self.logger.info(f"Provisioning result: {provisioning_result}")
                        self.logger.info(
                            f"Extracted resource_ids: {resource_ids} (type: { type(resource_ids)})"
                        )

                        # Store provider API information for later handler selection
                        if not hasattr(request, "metadata"):
                            request.metadata = {}
                        request.metadata["provider_api"] = template.provider_api or "RunInstances"
                        request.metadata["handler_used"] = provisioning_result.get(
                            "provider_data", {}
                        ).get("handler_used", "RunInstancesHandler")
                        self.logger.info(f"Stored provider API: {request.metadata['provider_api']}")

                        # Ensure resource_ids is actually a list
                        if isinstance(resource_ids, list):
                            for resource_id in resource_ids:
                                self.logger.info(
                                    f"Adding resource_id: {resource_id} (type: { type(resource_id)})"
                                )
                                if isinstance(resource_id, str):
                                    request = request.add_resource_id(resource_id)
                                else:
                                    self.logger.error(
                                        f"Expected string resource_id, got: { type(resource_id)} - {resource_id}"
                                    )
                        else:
                            self.logger.error(
                                f"Expected list for resource_ids, got: { type(resource_ids)} - {resource_ids}"
                            )

                        # Create machine aggregates for each instance
                        instance_data_list = provisioning_result.get("instances", [])
                        for instance_data in instance_data_list:
                            machine = self._create_machine_aggregate(
                                instance_data, request, template.template_id
                            )

                            # Save machine using UnitOfWork
                            with self.uow_factory.create_unit_of_work() as uow:
                                uow.machines.save(machine)

                        # Update request status based on fulfillment
                        if len(instance_data_list) == command.requested_count:
                            from src.domain.request.value_objects import RequestStatus

                            request = request.update_status(
                                RequestStatus.COMPLETED,
                                "All instances provisioned successfully",
                            )
                        elif len(instance_data_list) > 0:
                            from src.domain.request.value_objects import RequestStatus

                            request = request.update_status(
                                RequestStatus.PARTIAL,
                                f"Partially fulfilled: {len(instance_data_list)}/{command.requested_count} instances",
                            )
                        else:
                            from src.domain.request.value_objects import RequestStatus

                            request = request.update_status(
                                RequestStatus.IN_PROGRESS,
                                "Resources created, instances pending",
                            )
                    else:
                        # Handle provisioning failure
                        from src.domain.request.value_objects import RequestStatus

                        error_message = provisioning_result.get("error_message", "Unknown error")
                        request = request.update_status(
                            RequestStatus.FAILED,
                            f"Provisioning failed: {error_message}",
                        )
                        # Store detailed error in metadata for interface access
                        if not hasattr(request, "metadata"):
                            request.metadata = {}
                        request.metadata["error_message"] = error_message
                        request.metadata["error_type"] = "ProvisioningFailure"

                except Exception as provisioning_error:
                    # Handle unexpected provisioning errors
                    from src.domain.request.value_objects import RequestStatus

                    error_message = str(provisioning_error)
                    request = request.update_status(
                        RequestStatus.FAILED, f"Provisioning failed: {error_message}"
                    )
                    # Store detailed error in metadata for interface access
                    if not hasattr(request, "metadata"):
                        request.metadata = {}
                    request.metadata["error_message"] = error_message
                    request.metadata["error_type"] = type(provisioning_error).__name__

                    self.logger.error(
                        f"Provisioning failed for request { request.request_id}: {provisioning_error}"
                    )

        except Exception as provisioning_error:
            # Update request status to failed if request was created
            if request:
                from src.domain.request.value_objects import RequestStatus

                # CRITICAL FIX: Assign the returned updated request back to the variable
                request = request.update_status(
                    RequestStatus.FAILED,
                    f"Provisioning failed: {str(provisioning_error)}",
                )
                self.logger.error(
                    f"Provisioning failed for request { request.request_id}: {provisioning_error}"
                )

                # Save failed request for audit trail
                with self.uow_factory.create_unit_of_work() as uow:
                    events = uow.requests.save(request)
                    # Commit happens automatically when context manager exits

                # Publish events for failed request
                for event in events:
                    self.event_publisher.publish(event)

                # CRITICAL FIX: Raise exception instead of returning success
                raise ValueError(f"Machine provisioning failed: {str(provisioning_error)}")
            else:
                self.logger.error(f"Failed to create request: {provisioning_error}")
                raise

        # Only save and return success if we reach here (no exceptions)
        # Save request using UnitOfWork pattern (same as query handlers)
        with self.uow_factory.create_unit_of_work() as uow:
            events = uow.requests.save(request)
            # Commit happens automatically when context manager exits

        # Publish events
        for event in events:
            self.event_publisher.publish(event)

        self.logger.info(f"Machine request created successfully: {request.request_id}")
        return str(request.request_id)

    def _create_machine_aggregate(self, instance_data: Dict[str, Any], request, template_id: str):
        """Create machine aggregate from instance data."""
        from datetime import datetime

        from src.domain.base.value_objects import InstanceId, InstanceType
        from src.domain.machine.aggregate import Machine
        from src.domain.machine.machine_status import MachineStatus

        # Parse launch_time if it's a string
        launch_time = instance_data.get("launch_time")
        if isinstance(launch_time, str):
            try:
                launch_time = datetime.fromisoformat(launch_time.replace("Z", "+00:00"))
            except ValueError:
                launch_time = None

        return Machine(
            instance_id=InstanceId(value=instance_data["instance_id"]),
            request_id=str(request.request_id),
            template_id=template_id,
            provider_type="aws",
            instance_type=InstanceType(value=instance_data.get("instance_type", "t2.micro")),
            image_id=instance_data.get("image_id", "unknown"),
            status=MachineStatus.PENDING,
            private_ip=instance_data.get("private_ip"),
            public_ip=instance_data.get("public_ip"),
            launch_time=launch_time,
            metadata=instance_data.get("metadata", {}),
        )

    async def _execute_provisioning(self, template, request, selection_result) -> Dict[str, Any]:
        """Execute actual provisioning via selected provider using existing ProviderContext."""
        try:
            # Import required types (using existing imports)
            from src.providers.base.strategy import (
                ProviderOperation,
                ProviderOperationType,
            )

            # Create provider operation using existing pattern
            operation = ProviderOperation(
                operation_type=ProviderOperationType.CREATE_INSTANCES,
                parameters={
                    "template_config": template.to_dict(),
                    "count": request.requested_count,
                },
                context={
                    "correlation_id": str(request.request_id),
                    "dry_run": request.metadata.get("dry_run", False),
                },
            )

            # Execute operation using existing ProviderContext pattern
            # FIXED: Use correct strategy identifier format (aws-{instance_name})
            # The provider_instance from selection should match the registered
            # instance name
            strategy_identifier = (
                f"{selection_result.provider_type}-{selection_result.provider_instance}"
            )

            # Log available strategies for debugging
            available_strategies = self._provider_context.available_strategies
            self.logger.debug(f"Available strategies: {available_strategies}")
            self.logger.debug(f"Attempting to use strategy: {strategy_identifier}")

            result = await self._provider_context.execute_with_strategy(
                strategy_identifier, operation
            )

            # Process result using existing pattern
            if result.success:
                # Debug: Log the actual result structure
                self.logger.info(f"AWS provider result.data: {result.data}")
                self.logger.info(f"AWS provider result.metadata: {result.metadata}")

                # Extract resource_ids directly from result.data
                # The AWS provider should return resource_ids as a list
                resource_ids = result.data.get("resource_ids", [])
                instances = result.data.get("instances", [])

                self.logger.info(
                    f"Extracted resource_ids: {resource_ids} (type: { type(resource_ids)})"
                )
                self.logger.info(f"Extracted instances: {len(instances)} instances")

                # Log each resource ID for debugging
                if resource_ids:
                    for i, resource_id in enumerate(resource_ids):
                        self.logger.info(
                            f"Resource ID { i+ 1}: {resource_id} (type: { type(resource_id)})"
                        )

                return {
                    "success": True,
                    "resource_ids": resource_ids,
                    "instances": instances,
                    "provider_data": result.metadata or {},
                    "error_message": None,
                }
            else:
                return {
                    "success": False,
                    "resource_ids": [],
                    "instances": [],
                    "provider_data": result.metadata or {},
                    "error_message": result.error_message,
                }

        except Exception as e:
            self.logger.error(f"Provisioning execution failed: {e}")
            return {
                "success": False,
                "instance_ids": [],
                "provider_data": {},
                "error_message": str(e),
            }


@command_handler(CreateReturnRequestCommand)
class CreateReturnRequestHandler(BaseCommandHandler[CreateReturnRequestCommand, str]):
    """Handler for creating return requests."""

    def __init__(
        self,
        request_repository: RequestRepository,
        machine_repository: MachineRepository,
        template_repository,  # Add template repository
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, event_publisher, error_handler)
        self._request_repository = request_repository
        self._machine_repository = machine_repository
        self._template_repository = template_repository
        self._container = container

    async def validate_command(self, command: CreateReturnRequestCommand) -> None:
        """Validate create return request command."""
        await super().validate_command(command)
        if not command.machine_ids:
            raise ValueError("machine_ids is required and cannot be empty")

    async def execute_command(self, command: CreateReturnRequestCommand) -> str:
        """Handle return request creation command."""
        self.logger.info(f"Creating return request for machines: {command.machine_ids}")

        try:
            # Create return request aggregate
            # Get provider type from configuration using injected container
            from src.domain.request.aggregate import Request
            from src.domain.request.value_objects import RequestType

            config_manager = self._container.get(ConfigurationPort)
            provider_type = config_manager.get("provider.type", "aws")

            # Create return request with business logic
            # Use first machine's template if available, otherwise use generic return
            # template
            template_id = "return-machines"  # Business template for return operations
            if command.machine_ids:
                # Try to get template from first machine
                try:
                    machine = self._machine_repository.find_by_id(command.machine_ids[0])
                    if machine and machine.template_id:
                        template_id = f"return-{machine.template_id}"
                except Exception as e:
                    # Fallback to generic return template
                    self.logger.warning(
                        f"Failed to determine return template ID from machine: {e}",
                        extra={
                            "machine_ids": command.machine_ids,
                            "request_id": command.request_id,
                        },
                    )

            request = Request.create_new_request(
                request_type=RequestType.RETURN,
                template_id=template_id,
                machine_count=len(command.machine_ids),
                provider_type=provider_type,
                metadata=command.metadata or {},
            )

            # Save request and get extracted events
            events = self._request_repository.save(request)
            # Publish events
            for event in events:
                self.event_publisher.publish(event)

            self.logger.info(f"Return request created: {request.request_id}")
            return str(request.request_id)

        except Exception as e:
            self.logger.error(f"Failed to create return request: {e}")
            raise


@command_handler(UpdateRequestStatusCommand)
class UpdateRequestStatusHandler(BaseCommandHandler[UpdateRequestStatusCommand, None]):
    """Handler for updating request status."""

    def __init__(
        self,
        request_repository: RequestRepository,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, event_publisher, error_handler)
        self._request_repository = request_repository

    async def validate_command(self, command: UpdateRequestStatusCommand) -> None:
        """Validate update request status command."""
        await super().validate_command(command)
        if not command.request_id:
            raise ValueError("request_id is required")
        if not command.status:
            raise ValueError("status is required")

    async def execute_command(self, command: UpdateRequestStatusCommand) -> None:
        """Handle request status update command."""
        self.logger.info(f"Updating request status: {command.request_id} -> {command.status}")

        try:
            # Get request
            request = self._request_repository.get_by_id(command.request_id)
            if not request:
                raise EntityNotFoundError("Request", command.request_id)

            # Update status
            request.update_status(
                status=command.status,
                status_message=command.status_message,
                metadata=command.metadata,
            )

            # Save changes and get extracted events
            events = self._request_repository.save(request)
            # Publish events
            for event in events:
                self.event_publisher.publish(event)

            self.logger.info(f"Request status updated: {command.request_id} -> {command.status}")

        except EntityNotFoundError:
            self.logger.error(f"Request not found for status update: {command.request_id}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to update request status: {e}")
            raise


@command_handler(CancelRequestCommand)
class CancelRequestHandler(BaseCommandHandler[CancelRequestCommand, None]):
    """Handler for canceling requests."""

    def __init__(
        self,
        request_repository: RequestRepository,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, event_publisher, error_handler)
        self._request_repository = request_repository

    async def validate_command(self, command: CancelRequestCommand) -> None:
        """Validate cancel request command."""
        await super().validate_command(command)
        if not command.request_id:
            raise ValueError("request_id is required")

    async def execute_command(self, command: CancelRequestCommand) -> None:
        """Handle request cancellation command."""
        self.logger.info(f"Canceling request: {command.request_id}")

        try:
            # Get request
            request = self._request_repository.get_by_id(command.request_id)
            if not request:
                raise EntityNotFoundError("Request", command.request_id)

            # Cancel request
            request.cancel(reason=command.reason, metadata=command.metadata)

            # Save changes and get extracted events
            events = self._request_repository.save(request)
            # Publish events
            for event in events:
                self.event_publisher.publish(event)

            self.logger.info(f"Request canceled: {command.request_id}")

        except EntityNotFoundError:
            self.logger.error(f"Request not found for cancellation: {command.request_id}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to cancel request: {e}")
            raise


@command_handler(CompleteRequestCommand)
class CompleteRequestHandler(BaseCommandHandler[CompleteRequestCommand, None]):
    """Handler for completing requests."""

    def __init__(
        self,
        request_repository: RequestRepository,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, event_publisher, error_handler)
        self._request_repository = request_repository

    async def validate_command(self, command: CompleteRequestCommand) -> None:
        """Validate complete request command."""
        await super().validate_command(command)
        if not command.request_id:
            raise ValueError("request_id is required")

    async def execute_command(self, command: CompleteRequestCommand) -> None:
        """Handle request completion command."""
        self.logger.info(f"Completing request: {command.request_id}")

        try:
            # Get request
            request = self._request_repository.get_by_id(command.request_id)
            if not request:
                raise EntityNotFoundError("Request", command.request_id)

            # Complete request
            request.complete(result_data=command.result_data, metadata=command.metadata)

            # Save changes and get extracted events
            events = self._request_repository.save(request)
            # Publish events
            for event in events:
                self.event_publisher.publish(event)

            self.logger.info(f"Request completed: {command.request_id}")

        except EntityNotFoundError:
            self.logger.error(f"Request not found for completion: {command.request_id}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to complete request: {e}")
            raise
