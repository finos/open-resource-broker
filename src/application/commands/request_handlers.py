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
)
from domain.base.ports.scheduler_port import SchedulerPort
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
    ) -> None:
        """Initialize the instance."""
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory  # Use UoW factory pattern
        self._container = container
        self._query_bus = query_bus
        self._provider_selection_service = provider_selection_service
        self._provider_capability_service = provider_capability_service

    async def validate_command(self, command: CreateRequestCommand) -> None:
        """Validate create request command."""
        await super().validate_command(command)
        if not command.template_id:
            raise ValueError("template_id is required")
        if not command.requested_count or command.requested_count <= 0:
            raise ValueError("requested_count must be positive")

    async def execute_command(self, command: CreateRequestCommand) -> str:
        """Handle machine request creation command.

        Stages:
            1. Validate provider availability and initialize request state.
            2. Load template, select provider, and validate compatibility.
            3. Create request aggregate and handle dry-run fast path.
            4. Provision resources and reconcile machines/status from provider results.
            5. Persist the final request, publish events, and return the request id.
        """
        self.logger.info("Creating machine request for template: %s", command.template_id)

        # <1.> Validate provider availability and initialize request state.
        # CRITICAL VALIDATION: Ensure providers are available
        from providers.registry import get_provider_registry
        
        registry = get_provider_registry()
        available_providers = registry.get_registered_providers()
        available_instances = registry.get_registered_provider_instances()
        
        if not available_providers and not available_instances:
            error_msg = "No provider strategies available - cannot create machine requests"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        self.logger.debug(
            "Available provider types: %s, instances: %s",
            available_providers, available_instances
        )

        request = None

        try:
            # <2.> Load template, select provider, and validate compatibility.
            # Get template using CQRS QueryBus
            if not self._query_bus:
                raise ValueError("QueryBus is required for template lookup")

            from application.dto.queries import GetTemplateQuery

            template_query = GetTemplateQuery(template_id=command.template_id)
            template = await self._query_bus.execute(template_query)

            if not template:
                raise EntityNotFoundError("Template", command.template_id)

            self.logger.debug("Template found: %s (id=%s)", type(template), template.template_id)

            # Select provider based on template requirements
            selection_result = self._provider_selection_service.select_provider_for_template(
                template
            )
            self.logger.info(
                "Selected provider: %s (%s)",
                selection_result.provider_name,
                selection_result.selection_reason,
            )

            # Validate template compatibility with selected provider
            validation_result = self._provider_capability_service.validate_template_requirements(
                template, selection_result.provider_name, ValidationLevel.STRICT
            )

            if not validation_result.is_valid:
                error_msg = f"Template incompatible with provider {selection_result.provider_name}: {'; '.join(validation_result.errors)}"
                self.logger.error(error_msg)
                raise ValueError(error_msg)

            self.logger.info("Template validation passed: %s", validation_result.supported_features)

            # <3.> Create request aggregate and handle dry-run fast path.
            # Create request aggregate with selected provider
            from domain.request.aggregate import Request
            from domain.request.value_objects import RequestType

            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id=command.template_id,
                machine_count=command.requested_count,
                provider_type=selection_result.provider_type,
                provider_name=selection_result.provider_name,
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
                # <4.> Provision resources and reconcile machines/status from provider results.
                # Execute actual provisioning using selected provider
                try:
                    # Execute provisioning using selected provider
                    provisioning_result = await self._execute_provisioning(
                        template, request, selection_result
                    )

                    # Update request with provisioning results
                    if provisioning_result.get("success"):
                        # Store resource IDs and provider metadata
                        resource_ids = provisioning_result.get("resource_ids", [])
                        self.logger.info("Provisioning result: %s", provisioning_result)
                        self.logger.info(
                            "Extracted resource_ids: %s (type: %s)",
                            resource_ids,
                            type(resource_ids),
                        )

                        # Store provider API information for handler selection
                        if not hasattr(request, "metadata"):
                            request.metadata = {}
                        request.metadata["provider_api"] = template.provider_api or "RunInstances"
                        request.metadata["handler_used"] = provisioning_result.get(
                            "provider_data", {}
                        ).get("handler_used", "RunInstancesHandler")
                        self.logger.info(
                            "Stored provider API: %s", request.metadata["provider_api"]
                        )

                        # Add resource IDs to request
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
                            error_summary = (
                                "; ".join(
                                    f"{err.get('error_code', 'Unknown')}: {err.get('error_message', 'No message')}"
                                    for err in request.metadata.get("fleet_errors", [])
                                )
                                or "Unknown API errors"
                            )
                            if error_summary and "error_message" not in request.metadata:
                                request.metadata["error_message"] = error_summary
                                request.metadata["error_type"] = "ProvisioningPartialFailure"

                        # Store ASG capacity metadata for tracking
                        if template.provider_api == "ASG":
                            request.metadata.update(
                                {
                                    "asg_current_capacity": len(instance_data_list),
                                }
                            )

                            self.logger.info(
                                "Stored ASG capacity metadata for %s: desired=%s, actual=%s",
                                resource_ids[0] if resource_ids else "unknown",
                                request.requested_count,
                                len(instance_data_list),
                            )

                        machines_to_save = []
                        for instance_data in instance_data_list:
                            machine = self._create_machine_aggregate(
                                instance_data, request, template.template_id
                            )
                            machines_to_save.append(machine)

                        if machines_to_save:
                            with self.uow_factory.create_unit_of_work() as uow:
                                batch_events = uow.machines.save_batch(machines_to_save)
                            for event in batch_events:
                                self.event_publisher.publish(event)

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
                        # Store error details in metadata
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
                    # Store error details in metadata
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

                # Publish events for failed request
                for event in events:
                    self.event_publisher.publish(event)

                raise ValueError(f"Machine provisioning failed: {provisioning_error!s}")
            else:
                self.logger.error("Failed to create request: %s", provisioning_error)
                raise

        # Only save and return success if we reach here (no exceptions)
        # <5.> Persist the final request, publish events, and return the request id.
        # Save request using UnitOfWork pattern (same as query handlers)
        with self.uow_factory.create_unit_of_work() as uow:
            events = uow.requests.save(request)

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
        self.logger.debug("Creating machine aggregate instance_data: [%s]", instance_data)
        return Machine(
            instance_id=InstanceId(value=instance_data["instance_id"]),
            request_id=str(request.request_id),
            template_id=template_id,
            provider_type=request.provider_type,
            provider_name=request.provider_name,
            provider_api=request.metadata.get("provider_api") or request.provider_api,
            resource_id=instance_data.get("resource_id"),
            instance_type=InstanceType(value=instance_data.get("instance_type", "t2.micro")),
            image_id=instance_data.get("image_id", "unknown"),
            status=MachineStatus.PENDING,
            private_ip=instance_data.get("private_ip"),
            public_ip=instance_data.get("public_ip"),
            launch_time=launch_time,
            metadata=instance_data.get("metadata", {}),
        )

    async def _execute_provisioning(self, template, request, selection_result) -> dict[str, Any]:
        """Execute provisioning via selected provider using existing ProviderContext."""
        try:
            # Import required types
            from domain.base.ports.scheduler_port import SchedulerPort
            from providers.base.strategy import ProviderOperation, ProviderOperationType

            # Get scheduler for template formatting
            scheduler = self._container.get(SchedulerPort)

            # Create provider operation
            operation = ProviderOperation(
                operation_type=ProviderOperationType.CREATE_INSTANCES,
                parameters={
                    "template_config": scheduler.format_template_for_provider(template),
                    "count": request.requested_count,
                    "request_id": str(request.request_id),
                },
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
                    "dry_run": request.metadata.get("dry_run", False),
                },
            )

            # Resolve strategy identifier using registry
            from infrastructure.services.provider_strategy_resolver import ProviderStrategyResolver
            from providers.registry import get_provider_registry
            
            resolver = ProviderStrategyResolver()
            strategy_identifier = resolver.resolve_strategy_identifier(
                selection_result.provider_type,
                selection_result.provider_name
            )
            
            if not strategy_identifier:
                registry = get_provider_registry()
                available = registry.get_registered_providers() + registry.get_registered_provider_instances()
                error_msg = f"Provider strategy not found: {selection_result.provider_type}-{selection_result.provider_name}. Available: {available}"
                self.logger.error(error_msg)
                return {
                    "success": False,
                    "resource_ids": [],
                    "instances": [],
                    "provider_data": {},
                    "error_message": error_msg,
                }

            # Log available strategies for debugging
            self.logger.debug("Using registry-based strategy resolution")
            self.logger.debug("Attempting to use strategy: %s", strategy_identifier)

            # Get provider strategy from registry
            registry = get_provider_registry()
            if registry.is_provider_instance_registered(strategy_identifier):
                # Create strategy from instance
                provider_config = selection_result.provider_config or {}
                strategy = registry.create_strategy_from_instance(strategy_identifier, provider_config)
            elif registry.is_provider_registered(selection_result.provider_type):
                # Create strategy from type
                provider_config = selection_result.provider_config or {}
                strategy = registry.create_strategy(selection_result.provider_type, provider_config)
            else:
                available = registry.get_registered_providers() + registry.get_registered_provider_instances()
                error_msg = f"Provider strategy not found: {strategy_identifier}. Available: {available}"
                self.logger.error(error_msg)
                return {
                    "success": False,
                    "resource_ids": [],
                    "instances": [],
                    "provider_data": {},
                    "error_message": error_msg,
                }

            result = await strategy.execute_operation(operation)

            # Process result
            if result.success:
                # Extract resource information from result
                self.logger.info("AWS provider result.data: %s", result.data)
                self.logger.info("AWS provider result.metadata: %s", result.metadata)

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
        query_bus: QueryBus,  # Add QueryBus for template lookup
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory
        self._container = container
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
            from domain.request.aggregate import Request
            from domain.base.exceptions import EntityNotFoundError
            from collections import defaultdict

            # Group machines by provider to handle multi-provider returns
            provider_groups = defaultdict(list)
            
            with self.uow_factory.create_unit_of_work() as uow:
                for machine_id in command.machine_ids:
                    machine = uow.machines.get_by_id(machine_id)
                    if not machine:
                        raise EntityNotFoundError("Machine", machine_id)
                    
                    provider_key = (machine.provider_type, machine.provider_name)
                    provider_groups[provider_key].append(machine_id)

            # Create separate return requests for each provider
            created_requests = []
            for (provider_type, provider_name), machine_ids in provider_groups.items():
                request = Request.create_return_request(
                    instance_ids=machine_ids,
                    provider_type=provider_type,
                    provider_name=provider_name,
                    metadata=command.metadata or {},
                )
                
                with self.uow_factory.create_unit_of_work() as uow:
                    events = uow.requests.save(request)
                    for event in events:
                        self.event_publisher.publish(event)
                
                created_requests.append(str(request.request_id))
                self.logger.info(
                    "Return request created for provider %s: %s (%d machines)",
                    provider_name, request.request_id, len(machine_ids)
                )

                # Execute deprovisioning for this provider batch
                try:
                    provisioning_result = await self._execute_deprovisioning(
                        machine_ids, request
                    )
                    self.logger.info(f"Deprovisioning results for {provider_name}: {provisioning_result}")
                except Exception as e:
                    self.logger.error("Deprovisioning failed for provider %s request %s: %s", 
                                    provider_name, request.request_id, e)

            # Return first request ID for backward compatibility
            return created_requests[0] if created_requests else None

        except Exception as e:
            self.logger.error("Failed to create return request: %s", e)
            raise

        except Exception as e:
            self.logger.error("Failed to create return request: %s", e)
            raise

    async def _execute_deprovisioning(self, machine_ids: list[str], request) -> dict[str, Any]:
        """Execute deprovisioning - groups by provider context and resource."""

        try:
            # Group machines by (provider_name, provider_api, resource_id)
            from collections import defaultdict

            resource_groups = defaultdict(list)

            for machine_id in machine_ids:
                try:
                    with self.uow_factory.create_unit_of_work() as uow:
                        machine = uow.machines.find_by_id(machine_id)
                        if not machine:
                            raise ValueError(f"Machine not found: {machine_id}")
                        
                        # Use machine's actual provider context
                        group_key = (
                            machine.provider_name,
                            machine.provider_api or "RunInstances",  # Fallback for old machines
                            machine.resource_id
                        )
                        resource_groups[group_key].append(machine)
                        
                except Exception as e:
                    self.logger.error("Failed to get machine context for %s: %s", machine_id, e)
                    raise ValueError(f"Cannot determine context for machine {machine_id}: {e}")

            self.logger.info(
                "Grouped machines by resource context: %s",
                {f"{pn}-{pa}-{rid}": len(machines) for (pn, pa, rid), machines in resource_groups.items()},
            )

            # Create tasks for parallel execution
            import asyncio

            tasks = []

            for (provider_name, provider_api, resource_id), machines in resource_groups.items():
                task = asyncio.create_task(
                    self._process_resource_group(
                        provider_name, provider_api, resource_id, machines, request
                    ),
                    name=f"terminate-{provider_name}-{provider_api}-{resource_id}",
                )
                tasks.append(task)

            # Execute all tasks in parallel
            self.logger.info("Executing %d termination operations in parallel", len(tasks))

            # Wait for all tasks to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            success_count = 0
            error_count = 0
            errors = []

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    error_count += 1
                    errors.append(str(result))
                    self.logger.error("Task %s failed: %s", tasks[i].get_name(), result)
                elif result.get("success", False):
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(result.get("error_message", "Unknown error"))

            self.logger.info(
                "Deprovisioning completed: %d successful, %d failed", success_count, error_count
            )

            return {
                "success": error_count == 0,
                "successful_operations": success_count,
                "failed_operations": error_count,
                "errors": errors,
            }

        except Exception as e:
            self.logger.error("Parallel deprovisioning execution failed: %s", e, exc_info=True)
            return {"success": False, "error_message": str(e)}

    async def _process_resource_group(
        self,
        provider_name: str,
        provider_api: str,
        resource_id: str,
        machines: list,
        request,
    ) -> dict[str, Any]:
        """Process machines from same resource for termination."""

        try:
            instance_ids = [machine.instance_id.value for machine in machines]
            template_id = machines[0].template_id
            
            self.logger.info(
                "Processing resource group %s-%s-%s with %d machines", 
                provider_name, provider_api, resource_id, len(machines)
            )

            # Get template for configuration
            from application.dto.queries import GetTemplateQuery

            template_query = GetTemplateQuery(template_id=template_id)
            template = await self._query_bus.execute(template_query)

            if not template:
                raise ValueError(f"Template not found: {template_id}")

            # Get scheduler for template formatting
            from domain.base.ports.scheduler_port import SchedulerPort
            scheduler = self._container.get(SchedulerPort)
            template_config = scheduler.format_template_for_provider(template)
            
            self.logger.info("Using %s handler for resource %s", provider_api, resource_id)

            # Create operation using machine's actual provider context
            from providers.base.strategy import ProviderOperation, ProviderOperationType

            operation = ProviderOperation(
                operation_type=ProviderOperationType.TERMINATE_INSTANCES,
                parameters={
                    "instance_ids": instance_ids,
                    "template_config": template_config,
                    "template_id": template_id,
                    "provider_api": provider_api,
                    "resource_id": resource_id,
                },
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
                },
            )

            # Execute via provider registry
            from infrastructure.services.provider_strategy_resolver import ProviderStrategyResolver
            from providers.registry import get_provider_registry
            
            resolver = ProviderStrategyResolver()
            strategy_identifier = resolver.resolve_strategy_identifier("aws", provider_name)
            
            if not strategy_identifier:
                raise ValueError(f"Strategy not found for provider: {provider_name}")
            
            registry = get_provider_registry()
            
            # Get provider strategy from registry
            if registry.is_provider_instance_registered(strategy_identifier):
                # Create strategy from instance
                provider_config = {}  # Would need actual config from machine context
                strategy = registry.create_strategy_from_instance(strategy_identifier, provider_config)
            elif registry.is_provider_registered("aws"):
                # Create strategy from type
                provider_config = {}  # Would need actual config from machine context
                strategy = registry.create_strategy("aws", provider_config)
            else:
                raise ValueError(f"Strategy not found for provider: {provider_name}")

            result = await strategy.execute_operation(operation)

            if result.success:
                self.logger.info("Successfully terminated %d instances in resource %s", 
                               len(instance_ids), resource_id)
                return {"success": True, "terminated_instances": len(instance_ids)}
            else:
                self.logger.error("Termination failed for resource %s: %s", 
                                resource_id, result.error_message)
                return {"success": False, "error_message": result.error_message}

        except Exception as e:
            self.logger.error("Failed to process resource group %s: %s", resource_id, e)
            return {"success": False, "error_message": str(e)}

    def _get_provider_context(self):
        """Get provider context for operations - deprecated, use registry instead."""
        # This method is kept for backward compatibility but should be phased out
        try:
            from providers.base.strategy.provider_context import ProviderContext
            return self._container.get(ProviderContext)
        except Exception:
            return None


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
