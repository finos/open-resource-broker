"""Command handlers for request operations."""

from typing import Any, Optional

from application.base.handlers import BaseCommandHandler
from application.decorators import command_handler
from application.dto.commands import (
    CancelRequestCommand,
    CompleteRequestCommand,
    CreateRequestCommand,
    CreateReturnRequestCommand,
    UpdateRequestStatusCommand,
)
from application.services.provider_capability_service import (
    ProviderCapabilityService,
    ValidationLevel,
)
from application.services.provider_selection_service import ProviderSelectionService
from domain.base import UnitOfWorkFactory
from domain.base.exceptions import EntityNotFoundError
from domain.base.ports import (
    ContainerPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
    ProviderPort,
)
from domain.request.repository import RequestRepository
from infrastructure.di.buses import QueryBus


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
    ) -> None:
        """Initialize the instance."""
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory  # Use UoW factory pattern
        self._container = container
        self._query_bus = query_bus
        self._provider_selection_service = provider_selection_service
        self._provider_capability_service = provider_capability_service
        self._provider_context = provider_port

    async def validate_command(self, command: CreateRequestCommand) -> None:
        """Validate create request command."""
        await super().validate_command(command)
        if not command.template_id:
            raise ValueError("template_id is required")
        if not command.requested_count or command.requested_count <= 0:
            raise ValueError("requested_count must be positive")

    async def execute_command(self, command: CreateRequestCommand) -> str:
        """Handle machine request creation command."""
        self.logger.info("Creating machine request for template: %s", command.template_id)

        # CRITICAL VALIDATION: Ensure providers are available
        if not self._provider_context.available_strategies:
            error_msg = "No provider strategies available - cannot create machine requests"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        self.logger.debug(
            "Available provider strategies: %s",
            self._provider_context.available_strategies,
        )

        # Initialize request variable
        request = None

        try:
            # Get template using CQRS QueryBus
            if not self._query_bus:
                raise ValueError("QueryBus is required for template lookup")

            from application.dto.queries import GetTemplateQuery

            template_query = GetTemplateQuery(template_id=command.template_id)
            template = await self._query_bus.execute(template_query)

            if not template:
                raise EntityNotFoundError("Template", command.template_id)

            self.logger.debug("Template found: %s %s", type(template), template.to_dict())

            # Select provider based on template requirements
            selection_result = self._provider_selection_service.select_provider_for_template(
                template
            )
            self.logger.info(
                "Selected provider: %s (%s)",
                selection_result.provider_instance,
                selection_result.selection_reason,
            )

            # Validate template compatibility with selected provider
            validation_result = self._provider_capability_service.validate_template_requirements(
                template, selection_result.provider_instance, ValidationLevel.STRICT
            )

            if not validation_result.is_valid:
                error_msg = f"Template incompatible with provider {selection_result.provider_instance}: {'; '.join(validation_result.errors)}"
                self.logger.error(error_msg)
                raise ValueError(error_msg)

            self.logger.info("Template validation passed: %s", validation_result.supported_features)

            # Create request aggregate with selected provider
            from domain.request.aggregate import Request
            from domain.request.value_objects import RequestType

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
                request_id=command.request_id,  # Use the provided request_id (None if not provided)
            )

            # Check if this is a dry-run request
            is_dry_run = request.metadata.get("dry_run", False)

            if is_dry_run:
                # In dry-run mode, skip actual provisioning
                self.logger.info(
                    "Skipping actual provisioning for request %s (dry-run mode)",
                    request.request_id,
                )
                from domain.request.value_objects import RequestStatus

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
                        self.logger.info("Provisioning result: %s", provisioning_result)
                        self.logger.info(
                            "Extracted resource_ids: %s (type: %s)",
                            resource_ids,
                            type(resource_ids),
                        )

                        # Store provider API information for later handler selection
                        if not hasattr(request, "metadata"):
                            request.metadata = {}
                        request.metadata["provider_api"] = template.provider_api or "RunInstances"
                        request.metadata["handler_used"] = provisioning_result.get(
                            "provider_data", {}
                        ).get("handler_used", "RunInstancesHandler")
                        self.logger.info(
                            "Stored provider API: %s", request.metadata["provider_api"]
                        )

                        # Ensure resource_ids is actually a list
                        if isinstance(resource_ids, list):
                            for resource_id in resource_ids:
                                self.logger.info(
                                    "Adding resource_id: %s (type: %s)",
                                    resource_id,
                                    type(resource_id),
                                )
                                if isinstance(resource_id, str):
                                    request = request.add_resource_id(resource_id)
                                else:
                                    self.logger.error(
                                        "Expected string resource_id, got: %s - %s",
                                        type(resource_id),
                                        resource_id,
                                    )
                        else:
                            self.logger.error(
                                "Expected list for resource_ids, got: %s - %s",
                                type(resource_ids),
                                resource_ids,
                            )

                        # Create machine aggregates for each instance
                        instance_data_list = provisioning_result.get("instances", [])
                        provider_data = provisioning_result.get("provider_data", {})

                        # Preserve provider errors (if any) for partial success handling
                        if isinstance(provider_data, dict):
                            provider_errors = provider_data.get("fleet_errors") or []
                            if provider_errors and not request.metadata.get("fleet_errors"):
                                request.metadata["fleet_errors"] = provider_errors

                        has_api_errors = bool(request.metadata.get("fleet_errors"))
                        error_summary = None
                        if has_api_errors:
                            error_summary = "; ".join(
                                f"{err.get('error_code', 'Unknown')}: {err.get('error_message', 'No message')}"
                                for err in request.metadata.get("fleet_errors", [])
                            ) or "Unknown API errors"
                            if error_summary and "error_message" not in request.metadata:
                                request.metadata["error_message"] = error_summary
                                request.metadata["error_type"] = "ProvisioningPartialFailure"

                        # Store ASG-specific metadata for capacity tracking (after instance_data_list is defined)
                        if template.provider_api == "ASG":
                            request.metadata.update(
                                {
                                    "asg_current_capacity": len(
                                        instance_data_list
                                    ),  # Changes over time
                                }
                            )

                            self.logger.info(
                                "Stored ASG capacity metadata for %s: desired=%s, actual=%s",
                                resource_ids[0] if resource_ids else "unknown",
                                request.requested_count,
                                len(instance_data_list),
                            )

                        for instance_data in instance_data_list:
                            machine = self._create_machine_aggregate(
                                instance_data, request, template.template_id
                            )

                            # Save machine using UnitOfWork
                            with self.uow_factory.create_unit_of_work() as uow:
                                uow.machines.save(machine)

                        # Update request status based on fulfillment and API errors
                        if len(instance_data_list) == command.requested_count:
                            from domain.request.value_objects import RequestStatus

                            if has_api_errors:
                                request = request.update_status(
                                    RequestStatus.PARTIAL,
                                    f"Partial success: {len(instance_data_list)}/{command.requested_count} instances created with API errors: {error_summary}",
                                )
                            else:
                                request = request.update_status(
                                    RequestStatus.COMPLETED,
                                    "All instances provisioned successfully",
                                )
                        elif len(instance_data_list) > 0:
                            from domain.request.value_objects import RequestStatus

                            if has_api_errors:
                                request = request.update_status(
                                    RequestStatus.PARTIAL,
                                    f"Partial success: {len(instance_data_list)}/{command.requested_count} instances created with API errors: {error_summary}",
                                )
                            else:
                                request = request.update_status(
                                    RequestStatus.PARTIAL,
                                    f"Partially fulfilled: {len(instance_data_list)}/{command.requested_count} instances",
                                )
                        else:
                            from domain.request.value_objects import RequestStatus

                            request = request.update_status(
                                RequestStatus.IN_PROGRESS,
                                "Resources created, instances pending",
                            )
                    else:
                        # Handle provisioning failure
                        from domain.request.value_objects import RequestStatus

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
                    from domain.request.value_objects import RequestStatus

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
                        "Provisioning failed for request %s: %s",
                        request.request_id,
                        provisioning_error,
                    )

        except Exception as provisioning_error:
            # Update request status to failed if request was created
            if request:
                from domain.request.value_objects import RequestStatus

                # CRITICAL FIX: Assign the returned updated request back to the variable
                request = request.update_status(
                    RequestStatus.FAILED,
                    f"Provisioning failed: {provisioning_error!s}",
                )
                self.logger.error(
                    "Provisioning failed for request %s: %s",
                    request.request_id,
                    provisioning_error,
                )

                # Save failed request for audit trail
                with self.uow_factory.create_unit_of_work() as uow:
                    events = uow.requests.save(request)
                    # Commit happens automatically when context manager exits

                # Publish events for failed request
                for event in events:
                    self.event_publisher.publish(event)

                # CRITICAL FIX: Raise exception instead of returning success
                raise ValueError(f"Machine provisioning failed: {provisioning_error!s}")
            else:
                self.logger.error("Failed to create request: %s", provisioning_error)
                raise

        # Only save and return success if we reach here (no exceptions)
        # Save request using UnitOfWork pattern (same as query handlers)
        with self.uow_factory.create_unit_of_work() as uow:
            events = uow.requests.save(request)
            # Commit happens automatically when context manager exits

        # Publish events
        for event in events:
            self.event_publisher.publish(event)

        self.logger.info("Machine request created successfully: %s", request.request_id)
        return str(request.request_id)

    def _create_machine_aggregate(self, instance_data: dict[str, Any], request, template_id: str):
        """Create machine aggregate from instance data."""
        from datetime import datetime

        from domain.base.value_objects import InstanceId, InstanceType
        from domain.machine.aggregate import Machine
        from domain.machine.machine_status import MachineStatus

        # Parse launch_time if it's a string
        launch_time = instance_data.get("launch_time")
        if isinstance(launch_time, str):
            try:
                launch_time = datetime.fromisoformat(launch_time.replace("Z", "+00:00"))
            except ValueError:
                launch_time = None
        self.logger.debug("_create_machine_aggregate instance_data: [%s]", instance_data)
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

    async def _execute_provisioning(self, template, request, selection_result) -> dict[str, Any]:
        """Execute actual provisioning via selected provider using existing ProviderContext."""
        try:
            # Import required types (using existing imports)
            from providers.base.strategy import ProviderOperation, ProviderOperationType

            # Create provider operation using existing pattern
            operation = ProviderOperation(
                operation_type=ProviderOperationType.CREATE_INSTANCES,
                parameters={
                    "template_config": template.to_dict(),
                    "count": request.requested_count,
                    "request_id": str(request.request_id),
                },
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
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
            self.logger.debug("Available strategies: %s", available_strategies)
            self.logger.debug("Attempting to use strategy: %s", strategy_identifier)

            result = await self._provider_context.execute_with_strategy(
                strategy_identifier, operation
            )

            # Process result using existing pattern
            if result.success:
                # Debug: Log the actual result structure
                self.logger.info("AWS provider result.data: %s", result.data)
                self.logger.info("AWS provider result.metadata: %s", result.metadata)

                # Extract resource_ids directly from result.data
                # The AWS provider should return resource_ids as a list
                resource_ids = result.data.get("resource_ids", [])
                instances = result.data.get("instances", [])

                self.logger.info(
                    "Extracted resource_ids: %s (type: %s)",
                    resource_ids,
                    type(resource_ids),
                )
                self.logger.info("Extracted instances: %s instances", len(instances))

                # Log each resource ID for debugging
                if resource_ids:
                    for i, resource_id in enumerate(resource_ids):
                        self.logger.info(
                            "Resource ID %s: %s (type: %s)",
                            i + 1,
                            resource_id,
                            type(resource_id),
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
            self.logger.error("Provisioning execution failed: %s", e)
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
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        provider_port: ProviderPort,
        query_bus: QueryBus,  # Add QueryBus for template lookup
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory
        self._container = container
        self._provider_context = provider_port
        self._query_bus = query_bus

    async def validate_command(self, command: CreateReturnRequestCommand):
        """Validate create return request command."""
        await super().validate_command(command)
        if not command.machine_ids:
            raise ValueError("machine_ids is required and cannot be empty")

    async def execute_command(self, command: CreateReturnRequestCommand) -> str:
        """Handle return request creation command."""
        self.logger.info("Creating return request for machines: %s", command.machine_ids)

        try:
            # Create return request aggregate
            # Get provider type from configuration using injected container
            from domain.request.aggregate import Request

            # config_manager = self._container.get(ConfigurationPort)
            # provider_type = config_manager.get_provider_config("provider.type", "aws")
            # provider_config = config_manager.get_provider_config()
            provider_type = "aws"  # KBG TODO
            # Create return request with business logic
            # Use first machine's template if available, otherwise use generic return
            # template
            # template_id = "return-machines"  # Business template for return operations
            # if command.machine_ids:
            #     # Try to get template from first machine
            #     try:
            #         with self.uow_factory.create_unit_of_work() as uow:
            #             machine = self.machines.find_by_id(command.machine_ids[0])

            #     except Exception as e:
            #         # Fallback to generic return template
            #         self.logger.warning(
            #             "Failed to determine return template ID from machine: %s",
            #             e,
            #             extra={"machine_ids": command.machine_ids},
            #         )

            request = Request.create_return_request(
                instance_ids=command.machine_ids,
                provider_type=provider_type,
                metadata=command.metadata or {},
            )

            # Add machine IDs to the request
            # from domain.base.value_objects import InstanceId
            # for machine_id in command.machine_ids:
            #     request = request.add_instance_id(InstanceId(value=machine_id))
            # KBG TODO

            with self.uow_factory.create_unit_of_work() as uow:
                events = uow.requests.save(request)
                for event in events:
                    self.event_publisher.publish(event)

            self.logger.info("Return request created: %s", request.request_id)

            try:
                provisioning_result = await self._execute_deprovisioning(
                    command.machine_ids, request
                )
                self.logger.info(f"Provisioning results: {provisioning_result}")

            except Exception as e:
                # Handle provisioning errors
                # Log the error and raise a custom exception
                self.logger.error("Provisioning failed for return request: %s", request.request_id)
                raise ValueError("Provisioning failed for return request") from e

            return str(request.request_id)

        except Exception as e:
            self.logger.error("Failed to create return request: %s", e)
            raise

    async def _execute_deprovisioning(self, machine_ids: list[str], request) -> dict[str, Any]:
        """Execute De-Provisioning - handles instances from multiple templates in parallel"""

        try:
            # Step 1: Group instances by their original template
            from collections import defaultdict

            template_groups = defaultdict(list)

            for machine_id in machine_ids:
                try:
                    with self.uow_factory.create_unit_of_work() as uow:
                        machine = uow.machines.find_by_id(machine_id)
                        if not machine:
                            raise ValueError(f"Machine not found: {machine_id}")
                        if not machine.template_id:
                            raise ValueError(f"Machine {machine_id} has no template_id")
                        template_groups[machine.template_id].append(machine_id)
                except Exception as e:
                    self.logger.error("Failed to get template for machine %s: %s", machine_id, e)
                    raise ValueError(f"Cannot determine template for machine {machine_id}: {e}")

            self.logger.info(
                "Grouped instances by template: %s",
                {tid: len(instances) for tid, instances in template_groups.items()},
            )

            # Step 2: Create instance ID to resource ID mapping
            resource_mapping = self._get_instance_ids_to_resource_id_mapping(machine_ids)

            # Step 3: Create tasks for parallel execution
            import asyncio

            tasks = []

            for template_id, instance_group in template_groups.items():
                # Filter mapping for this template group
                template_mapping = {
                    instance_id: resource_mapping.get(instance_id, (None, 0))
                    for instance_id in instance_group
                }

                task = asyncio.create_task(
                    self._process_template_group(
                        template_id, instance_group, request, template_mapping
                    ),
                    name=f"terminate-{template_id}",
                )
                tasks.append(task)

            # Step 3: Execute all tasks in parallel
            self.logger.info("Executing %d termination operations in parallel", len(tasks))

            # Wait for all tasks to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Step 4: Process results and handle exceptions
            processed_results = []
            total_success = 0

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    template_id = list(template_groups.keys())[i]
                    instance_group = list(template_groups.values())[i]
                    self.logger.error("Task for template %s failed: %s", template_id, result)
                    processed_results.append(
                        {
                            "template_id": template_id,
                            "instance_count": len(instance_group),
                            "instance_ids": instance_group,
                            "success": False,
                            "error_message": str(result),
                        }
                    )
                else:
                    processed_results.append(result)
                    if result.get("success", False):
                        total_success += len(result.get("instance_ids", []))

            # Step 5: Return comprehensive results
            overall_success = total_success == len(machine_ids)

            self.logger.info(
                "Parallel de-provisioning completed: %d successful, %d failed out of %d total instances",
                total_success,
                len(machine_ids) - total_success,
                len(machine_ids),
            )

            return {
                "success": overall_success,
                "total_instances": len(machine_ids),
                "successful_instances": total_success,
                "failed_instances": len(machine_ids) - total_success,
                "results_by_template": processed_results,
                "handlers_used": list(
                    set(r.get("provider_api") for r in processed_results if r.get("provider_api"))
                ),
                "parallel_execution": True,
                "concurrent_operations": len(tasks),
            }

        except Exception as e:
            self.logger.error("Parallel de-provisioning execution failed: %s", e, exc_info=True)
            return {"success": False, "error_message": str(e)}

    def _get_instance_ids_to_resource_id_mapping(
        self, machine_ids: list[str]
    ) -> dict[str, tuple[Optional[str], int]]:
        """
        Determine resource ID and desired capacity for each instance ID by looking up the machine's request_id
        in the database and getting the first resource_id and desired_capacity from that request.

        Args:
            machine_ids: List of instance IDs to get resource IDs for

        Returns:
            Dictionary mapping instance_id -> (resource_id or None, desired_capacity or 0)
        """
        mapping: dict[str, tuple[Optional[str], int]] = {}

        for machine_id in machine_ids:
            resource_id = None
            desired_capacity = 0
            try:
                with self.uow_factory.create_unit_of_work() as uow:
                    # Get the machine from database
                    machine = uow.machines.find_by_id(machine_id)
                    if machine and machine.request_id:
                        # Get the request from database
                        from domain.request.value_objects import RequestId

                        request_id = RequestId(value=machine.request_id)
                        request = uow.requests.get_by_id(request_id)

                        if request:
                            # Get first resource_id from the request
                            if request.resource_ids:
                                resource_id = request.resource_ids[0]
                                self.logger.debug(
                                    "Found resource_id %s for instance %s via request %s",
                                    resource_id,
                                    machine_id,
                                    machine.request_id,
                                )
                            else:
                                self.logger.warning(
                                    "No resource_ids found for request %s (machine %s)",
                                    machine.request_id,
                                    machine_id,
                                )

                            # Get desired_capacity from the request
                            desired_capacity = getattr(request, "desired_capacity", 0)
                            self.logger.debug(
                                "Found desired_capacity %s for instance %s via request %s",
                                desired_capacity,
                                machine_id,
                                machine.request_id,
                            )
                        else:
                            self.logger.warning(
                                "Request %s not found for machine %s",
                                machine.request_id,
                                machine_id,
                            )
                    else:
                        self.logger.warning("Machine %s not found or has no request_id", machine_id)

            except Exception as e:
                self.logger.error(
                    "Failed to get resource_id and desired_capacity for instance %s: %s",
                    machine_id,
                    e,
                )

            mapping[machine_id] = (resource_id, desired_capacity)

        self.logger.info(
            "Created instance to resource ID mapping for %d instances: %s",
            len(mapping),
            [(iid, rid) for iid, (rid, _) in mapping.items() if rid is not None],
        )

        return mapping

    async def _process_template_group(
        self,
        template_id: str,
        instance_group: list[str],
        request,
        resource_mapping: dict[str, tuple[Optional[str], int]],
    ) -> dict[str, Any]:
        """Process a single template group - designed for parallel execution

        Args:
            template_id: The template ID for this group
            instance_group: List of instance IDs to process
            request: The request object
            resource_mapping: Dict mapping instance_id to (resource_id or None, desired_capacity)
        """

        try:
            self.logger.info(
                "Processing template group %s with %d instances", template_id, len(instance_group)
            )
            self.logger.debug("Instance to resource ID mapping: %s", resource_mapping)

            # Get the actual template configuration
            from application.dto.queries import GetTemplateQuery

            template_query = GetTemplateQuery(template_id=template_id)
            template = await self._query_bus.execute(template_query)

            if not template:
                raise ValueError(f"Template not found: {template_id}")

            template_config = template.to_dict()
            provider_api = template.provider_api
            self.logger.info("Using %s handler for template %s", provider_api, template_id)

            # Create operation for this specific template group
            from providers.base.strategy import ProviderOperation, ProviderOperationType

            operation = ProviderOperation(
                operation_type=ProviderOperationType.TERMINATE_INSTANCES,
                parameters={
                    "instance_ids": instance_group,
                    "template_config": template_config,
                    "template_id": template_id,
                    "provider_api": provider_api,
                    "resource_mapping": resource_mapping,
                },
                context={
                    "correlation_id": str(request.request_id),
                    "template_id": template_id,
                    "parallel_execution": True,
                },
            )

            # Execute termination for this group
            group_result = await self._provider_context.terminate_resources(
                instance_group, operation
            )

            # Handle case where terminate_resources returns None
            if group_result is None:
                self.logger.error("terminate_resources returned None for template %s", template_id)
                return {
                    "template_id": template_id,
                    "provider_api": provider_api,
                    "instance_count": len(instance_group),
                    "instance_ids": instance_group,
                    "success": False,
                    "error_message": "terminate_resources returned None - provider context error",
                }

            success = group_result.get("success", False)
            self.logger.info(
                "Template %s (%s): %s - %d instances",
                template_id,
                provider_api,
                "SUCCESS" if success else "FAILED",
                len(instance_group),
            )

            return {
                "template_id": template_id,
                "provider_api": provider_api,
                "instance_count": len(instance_group),
                "instance_ids": instance_group,
                "success": success,
                "error_message": group_result.get("error_message"),
            }

        except Exception as e:
            self.logger.error("Failed to process template group %s: %s", template_id, e)
            return {
                "template_id": template_id,
                "instance_count": len(instance_group),
                "instance_ids": instance_group,
                "success": False,
                "error_message": str(e),
            }


@command_handler(UpdateRequestStatusCommand)
class UpdateRequestStatusHandler(BaseCommandHandler[UpdateRequestStatusCommand, None]):
    """Handler for updating request status."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        request_repository: RequestRepository,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory
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
        self.logger.info("Updating request status: %s -> %s", command.request_id, command.status)

        try:
            # Find request in the storage
            with self.uow_factory.create_unit_of_work() as uow:
                request = uow.requests.find_by_id(command.request_id)
                if not request:
                    raise EntityNotFoundError("Request", command.request_id)

            # Update status
            request.update_status(
                status=command.status,
                status_message=command.status_message,
                metadata=command.metadata,
            )

            # Save changes and get extracted events
            with self.uow_factory.create_unit_of_work() as uow:
                events = self.requests.save(request)
                # Publish events
                for event in events:
                    self.event_publisher.publish(event)

            self.logger.info("Request status updated: %s -> %s", command.request_id, command.status)

        except EntityNotFoundError:
            self.logger.error("Request not found for status update: %s", command.request_id)
            raise
        except Exception as e:
            self.logger.error("Failed to update request status: %s", e)
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
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._request_repository = request_repository

    async def validate_command(self, command: CancelRequestCommand) -> None:
        """Validate cancel request command."""
        await super().validate_command(command)
        if not command.request_id:
            raise ValueError("request_id is required")

    async def execute_command(self, command: CancelRequestCommand) -> None:
        """Handle request cancellation command."""
        self.logger.info("Canceling request: %s", command.request_id)

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

            self.logger.info("Request canceled: %s", command.request_id)

        except EntityNotFoundError:
            self.logger.error("Request not found for cancellation: %s", command.request_id)
            raise
        except Exception as e:
            self.logger.error("Failed to cancel request: %s", e)
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
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._request_repository = request_repository

    async def validate_command(self, command: CompleteRequestCommand) -> None:
        """Validate complete request command."""
        await super().validate_command(command)
        if not command.request_id:
            raise ValueError("request_id is required")

    async def execute_command(self, command: CompleteRequestCommand) -> None:
        """Handle request completion command."""
        self.logger.info("Completing request: %s", command.request_id)

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

            self.logger.info("Request completed: %s", command.request_id)

        except EntityNotFoundError:
            self.logger.error("Request not found for completion: %s", command.request_id)
            raise
        except Exception as e:
            self.logger.error("Failed to complete request: %s", e)
            raise
