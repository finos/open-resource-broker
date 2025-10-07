"""Query handlers for application services."""

from __future__ import annotations

from typing import Any, TypeVar

from application.base.handlers import BaseQueryHandler
from application.decorators import query_handler
from application.dto.queries import (
    GetMachineQuery,
    GetRequestQuery,
    GetRequestStatusQuery,
    GetTemplateQuery,
    ListActiveRequestsQuery,
    ListMachinesQuery,
    ListReturnRequestsQuery,
    ListTemplatesQuery,
    ValidateTemplateQuery,
)
from application.dto.responses import MachineDTO, RequestDTO
from application.dto.system import ValidationDTO
from domain.base import UnitOfWorkFactory

# Exception handling through BaseQueryHandler (Clean Architecture compliant)
from domain.base.exceptions import EntityNotFoundError
from domain.base.ports import ContainerPort, ErrorHandlingPort, LoggingPort
from domain.template.aggregate import Template

T = TypeVar("T")


# Query handlers
@query_handler(GetRequestQuery)
class GetRequestHandler(BaseQueryHandler[GetRequestQuery, RequestDTO]):
    """Handler for getting request details with machine status checking."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
    ) -> None:
        """Initialize the instance."""
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._container = container
        self._cache_service = self._get_cache_service()
        self.event_publisher = self._get_event_publisher()

    async def execute_query(self, query: GetRequestQuery) -> RequestDTO:
        """Execute get request query with machine status checking and caching."""
        self.logger.info("Getting request details for: %s", query.request_id)

        try:
            # Check cache first if enabled
            if self._cache_service and self._cache_service.is_caching_enabled():
                cached_result = self._cache_service.get_cached_request(query.request_id)
                if cached_result:
                    self.logger.info("Cache hit for request: %s", query.request_id)
                    return cached_result

            # Cache miss - get request from storage
            machine_ids_from_db = []

            with self.uow_factory.create_unit_of_work() as uow:
                from domain.request.value_objects import RequestId

                request_id = RequestId(value=query.request_id)

                request = uow.requests.get_by_id(request_id)

                self.logger.debug(
                    f"Searching for request_id: {request_id} request object retrieved: {request}"
                )

                if not request:
                    raise EntityNotFoundError("Request", query.request_id)

                machine_ids_from_db = request.instance_ids
                if len(request.instance_ids) == 0:
                    self.logger.info("No machines in request, returning empty list")

            # Fetch actual full Machine object from DB
            machine_obj_from_db = []
            with self.uow_factory.create_unit_of_work() as uow:
                for m in machine_ids_from_db:
                    res = uow.machines.get_by_id(m)
                    machine_obj_from_db.append(res)

            self.logger.debug(f"Machines associated with this request in DB: {machine_obj_from_db}")

            # Update machine status if needed
            machine_objects_from_provider = []
            if not machine_obj_from_db and request.resource_ids:
                self.logger.info(
                    "DEBUG: No machines in storage but have resource IDs %s, checking provider",
                    request.resource_ids,
                )
                # No machines in storage but we have resource IDs - check provider and
                # create machines
<<<<<<< HEAD
                machine_objects_from_provider = await self._check_provider_and_create_machines(
                    request
                )
                self.logger.info(
                    "DEBUG: Provider check returned %s machines", len(machine_obj_from_db)
                )
            elif machine_obj_from_db:
                self.logger.info(
                    "DEBUG: Have %s machines, updating status from AWS", len(machine_obj_from_db)
                )
=======
                machine_objects_from_provider = await self._check_provider_and_create_machines(request)
                self.logger.info("Provider check returned %s machines", len(machine_obj_from_db))
            elif machine_obj_from_db:
                self.logger.info("Have %s machines, updating status from AWS", len(machine_obj_from_db))
>>>>>>> c27a705 (Refactoring logging)
                # We have machines - update their status from AWS
                machine_objects_from_provider = await self._update_machine_status_from_aws(
                    machine_obj_from_db
                )
            else:
                self.logger.info(
                    "DEBUG: No machines and no resource IDs for request %s",
                    query.request_id,
                )
            print(f"Machines from DB:  {machine_obj_from_db}")
            print(f"Machines from cloud provider:  {machine_objects_from_provider}")

            # Determine if request status needs updating based on machine states
            new_status, status_message = self._determine_request_status_from_machines(
                machine_obj_from_db, machine_objects_from_provider, request
            )

            # Update request status if needed
            if new_status:
                from domain.request.request_types import RequestStatus

                updated_request = request.update_status(RequestStatus(new_status), status_message)

                # Save updated request
                with self.uow_factory.create_unit_of_work() as uow:
                    uow.requests.save(updated_request)

                # Update the request object for DTO creation
                request = updated_request

            # Convert machines directly to DTOs
            from application.request.dto import MachineReferenceDTO

            machine_references = [
                MachineReferenceDTO(
                    machine_id=str(machine.instance_id.value),
                    name=machine.private_ip or str(machine.instance_id.value),
                    result=self._map_machine_status_to_result(machine.status.value),
                    status=machine.status.value,
                    private_ip_address=machine.private_ip or "",
                    public_ip_address=machine.public_ip,
                    launch_time=int(machine.launch_time.timestamp() if machine.launch_time else 0),
                )
                for machine in machine_objects_from_provider
            ]

            request_dto = RequestDTO(
                request_id=str(request.request_id),
                template_id=request.template_id,
                requested_count=request.requested_count,
                status=request.status.value,
                created_at=request.created_at,
                machine_references=machine_references,
                metadata=request.metadata or {},
            )

            # Cache the result if caching is enabled
            if self._cache_service and self._cache_service.is_caching_enabled():
                self._cache_service.cache_request(request_dto)

            self.logger.info(
                "Retrieved request with %s machines: %s",
                len(machine_references),
                query.request_id,
            )
            return request_dto

        except EntityNotFoundError:
            self.logger.error("Request not found: %s", query.request_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get request: %s", e)
            raise

    async def _get_machines_from_storage(self, request_id: str) -> list:
        """Get machines from storage for the request."""
        try:
            with self.uow_factory.create_unit_of_work() as uow:
                machines = uow.machines.find_by_request_id(request_id)
                return machines
        except Exception as e:
            self.logger.warning(
                "Failed to get machines from storage for request %s: %s", request_id, e
            )
            return []

    async def _check_provider_and_create_machines(self, request) -> list:
        """Check provider status and create machine aggregates using provider strategy pattern."""
        try:
            # Get provider context from container
            provider_context = self._get_provider_context()
            if not provider_context:
                self.logger.error("Provider context not available")
                return []

            # Create operation for resource-to-instance discovery using stored
            # provider API
            from providers.base.strategy import ProviderOperation, ProviderOperationType

            operation = ProviderOperation(
                operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
                parameters={
                    "resource_ids": request.resource_ids,
                    "provider_api": request.metadata.get("provider_api", "RunInstances"),
                    "template_id": request.template_id,
                },
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
                },
            )

            # Execute operation using provider context with correct strategy identifier
            strategy_identifier = f"{request.provider_type}-{request.provider_type}-{request.provider_instance or 'default'}"
            self.logger.info(
                "Using provider strategy: %s for request %s",
                strategy_identifier,
                request.request_id,
            )
            self.logger.info("Operation parameters: %s", operation.parameters)

            result = await provider_context.execute_with_strategy(strategy_identifier, operation)

            self.logger.info(
                "Provider strategy result: success=%s, data_keys=%s",
                result.success,
                list(result.data.keys()) if result.data else "None",
            )

            if not result.success:
                self.logger.warning(
                    "Failed to discover instances from resources: %s",
                    result.error_message,
                )
                return []

            # Get instance details from result
            instance_details = result.data.get("instances", [])
            if not instance_details:
                self.logger.info("No instances found for request %s", request.request_id)
                return []

            # Create machine aggregates from instance details
            machines = []
            for instance_data in instance_details:
                machine = self._create_machine_from_aws_data(instance_data, request)
                machines.append(machine)

            # Batch save machines for efficiency
            if machines:
                with self.uow_factory.create_unit_of_work() as uow:
                    # Save each machine individually
                    for machine in machines:
                        uow.machines.save(machine)

                    # Publish events for all machines
                    for machine in machines:
                        events = machine.get_domain_events()
                        for event in events:
                            self.event_publisher.publish(event)
                        machine.clear_domain_events()

                self.logger.info(
                    "Created and saved %s machines for request %s",
                    len(machines),
                    request.request_id,
                )

            return machines

        except Exception as e:
            self.logger.error("Failed to check provider and create machines: %s", e)
            return []

    async def _update_machine_status_from_aws(self, machines: list) -> list:
        """Update machine status from AWS using existing handler methods."""
        try:
            # Group machines by request to use existing check_hosts_status methods
            if not machines:
                return []

            print(f"KBG machine type: {type(machines[0])}")

            # Get the request for the first machine (all should be same request)
            request_id = str(machines[0].request_id)
            with self.uow_factory.create_unit_of_work() as uow:
                from domain.request.value_objects import RequestId

                request = uow.requests.get_by_id(RequestId(value=request_id))
                if not request:
                    return machines

            # Get provider context and check AWS status
            provider_context = self._get_provider_context()

            # Create operation to check instance status using instance IDs
            from providers.base.strategy import ProviderOperation, ProviderOperationType

            # Extract instance IDs from machines
            print("KBG --1")
            instance_ids = [str(machine.instance_id.value) for machine in machines]
            print(f"KBG --2: {instance_ids}")

            operation = ProviderOperation(
                operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
                parameters={
                    "instance_ids": instance_ids,
                    "template_id": request.template_id,
                },
                context={"correlation_id": str(request.request_id)},
            )

            # Execute operation using provider context
            # Use the correct strategy identifier format:
            # provider_type-provider_type-instance
            strategy_identifier = f"{request.provider_type}-{request.provider_type}-{request.provider_instance or 'default'}"
            print(f"KBG --3: {strategy_identifier}")
            # success
            result = await provider_context.execute_with_strategy(strategy_identifier, operation)
            print(f"KBG --4: {result}")
            if not result.success:
                self.logger.warning("Failed to check resource status: %s", result.error_message)
                return machines

            # Extract domain machine entities from result (provider strategy already
            # converted AWS data)
            domain_machines = result.data.get("machines", [])

            # Update machine status if changed
            updated_machines = []
            for machine in machines:
                domain_machine = next(
                    (
                        dm
                        for dm in domain_machines
                        if dm["instance_id"] == str(machine.instance_id.value)
                    ),
                    None,
                )

                if domain_machine:
                    # Provider strategy already converted AWS data to domain format
                    from domain.machine.machine_status import MachineStatus

                    new_status = MachineStatus(domain_machine["status"])

                    # Check if we need to update the machine (status or network info
                    # changed)
                    needs_update = (
                        machine.status != new_status
                        or machine.private_ip != domain_machine.get("private_ip")
                        or machine.public_ip != domain_machine.get("public_ip")
                    )

                    if needs_update:
                        # Create updated machine data using domain entity format
                        machine_data = machine.model_dump()
                        machine_data["status"] = new_status
                        machine_data["private_ip"] = domain_machine.get("private_ip")
                        machine_data["public_ip"] = domain_machine.get("public_ip")
                        machine_data["launch_time"] = domain_machine.get(
                            "launch_time", machine.launch_time
                        )
                        machine_data["version"] = machine.version + 1

                        # Create new machine instance with updated data
                        from domain.machine.aggregate import Machine

                        updated_machine = Machine.model_validate(machine_data)

                        # Save updated machine
                        with self.uow_factory.create_unit_of_work() as uow:
                            uow.machines.save(updated_machine)

                        updated_machines.append(updated_machine)
                    else:
                        updated_machines.append(machine)
                else:
                    # Domain machine not found - machine might be terminated
                    updated_machines.append(machine)

            return updated_machines

        except Exception as e:
            self.logger.warning("Failed to update machine status from AWS: %s", e)
            return machines

    def _get_provider_context(self):
        """Get provider context for AWS operations."""
        try:
            from providers.base.strategy.provider_context import ProviderContext

            return self._container.get(ProviderContext)
        except Exception:
            # Fallback - create a simple provider context
            return self._create_simple_provider_context()

    def _create_simple_provider_context(self):
        """Create a simple provider context for AWS operations."""

        class SimpleProviderContext:
            """Simple provider context for AWS operations."""

            def __init__(self, container) -> None:
                self.container = container

            async def check_resource_status(self, request) -> list[dict[str, Any]]:
                """Use appropriate AWS handler based on resource type."""
                aws_handler = self._get_aws_handler_for_request(request)
                return aws_handler.check_hosts_status(request)

            def _get_aws_handler_for_request(self, request):
                """Get appropriate AWS handler based on request/template."""
                if request.resource_ids:
                    # Use first resource_id for handler selection logic
                    resource_id = request.resource_ids[0]
                    if resource_id.startswith("fleet-"):
                        from providers.aws.infrastructure.handlers.ec2_fleet_handler import (
                            EC2FleetHandler,
                        )

                        return self.container.get(EC2FleetHandler)
                    elif resource_id.startswith("sfr-"):
                        from providers.aws.infrastructure.handlers.spot_fleet_handler import (
                            SpotFleetHandler,
                        )

                        return self.container.get(SpotFleetHandler)
                    elif resource_id.startswith("run-instances-"):
                        from providers.aws.infrastructure.handlers.run_instances_handler import (
                            RunInstancesHandler,
                        )

                        return self.container.get(RunInstancesHandler)
                    else:
                        from providers.aws.infrastructure.handlers.asg_handler import (
                            ASGHandler,
                        )

                        return self.container.get(ASGHandler)

                # Fallback to RunInstances
                from providers.aws.infrastructure.handlers.run_instances_handler import (
                    RunInstancesHandler,
                )

                return self.container.get(RunInstancesHandler)

        return SimpleProviderContext(self._container)

    def _create_machine_from_aws_data(self, aws_instance: dict[str, Any], request):
        """Create machine aggregate from AWS instance data."""
        from domain.base.value_objects import InstanceId
        from domain.machine.aggregate import Machine

        return Machine(
            instance_id=InstanceId(value=aws_instance["InstanceId"]),
            request_id=str(request.request_id),
            # Use first for backward compatibility
            resource_id=request.resource_ids[0] if request.resource_ids else None,
            template_id=request.template_id,
            provider_type="aws",
            status=self._map_aws_state_to_machine_status(aws_instance["State"]),
            private_ip=aws_instance.get("PrivateIpAddress"),
            public_ip=aws_instance.get("PublicIpAddress"),
            launch_time=aws_instance.get("LaunchTime"),
        )

    def _map_aws_state_to_machine_status(self, aws_state: str):
        """Map AWS instance state to machine status."""
        from domain.machine.machine_status import MachineStatus

        state_mapping = {
            "pending": MachineStatus.PENDING,
            "running": MachineStatus.RUNNING,
            "shutting-down": MachineStatus.SHUTTING_DOWN,
            "terminated": MachineStatus.TERMINATED,
            "stopping": MachineStatus.STOPPING,
            "stopped": MachineStatus.STOPPED,
        }

        return state_mapping.get(aws_state, MachineStatus.UNKNOWN)

    def _map_machine_status_to_result(self, status: str) -> str:
        """Map machine status to HostFactory result field."""
        # Per docs: "Possible values: 'executing', 'fail', 'succeed'"
        if status == "running":
            return "succeed"
        elif status in ["pending", "launching"]:
            return "executing"
        elif status in ["terminated", "failed", "error"]:
            return "fail"
        else:
            return "executing"  # Default for unknown states

    def _get_cache_service(self):
        """Get cache service for request caching."""
        try:
            from domain.base.ports import ConfigurationPort
            from infrastructure.caching.request_cache_service import RequestCacheService

            config_manager = self._container.get(ConfigurationPort)
            cache_service = RequestCacheService(
                uow_factory=self.uow_factory,
                config_manager=config_manager,
                logger=self.logger,
            )
            return cache_service
        except Exception as e:
            self.logger.warning("Failed to initialize cache service: %s", e)
            return None

    def _get_event_publisher(self):
        """Get event publisher for domain events."""
        try:
            from domain.base.ports import EventPublisherPort

            return self._container.get(EventPublisherPort)
        except Exception as e:
            self.logger.warning("Failed to initialize event publisher: %s", e)

            # Return a no-op event publisher
            class NoOpEventPublisher:
                """No-operation event publisher that discards events."""

                def publish(self, event) -> None:
                    """Publish event (no-op implementation)."""

            return NoOpEventPublisher()

    def _determine_request_status_from_machines(
        self, machine_objects_from_database: list, machine_objects_from_provider: list, request
    ) -> tuple[str, str]:
        """
        Compare machine objects from database and provider to determine if request status needs updating.
        Handles both ACQUIRE and RETURN request types with different logic.

        Args:
            machine_objects_from_database: List of machine objects from local database
            machine_objects_from_provider: List of machine objects from cloud provider
            request: The request object containing requested_count, current status, and request_type

        Returns:
            Tuple of (new_status, status_message) or (None, None) if no change needed
        """
        from domain.machine.machine_status import MachineStatus
        from domain.request.request_types import RequestStatus, RequestType

        try:
            requested_count = request.requested_count
            current_status = request.status
            request_type = request.request_type

            # Count machines by status from provider data (most up-to-date)
            provider_machine_count = len(machine_objects_from_provider)
            database_machine_count = len(machine_objects_from_database)

            # Analyze provider machine statuses
            running_count = 0
            pending_count = 0
            failed_count = 0
            terminated_count = 0
            shutting_down_count = 0

            for machine in machine_objects_from_provider:
                status = machine.status if hasattr(machine, "status") else machine.get("status")
                if isinstance(status, str):
                    status = MachineStatus(status)

                if status == MachineStatus.RUNNING:
                    running_count += 1
                elif status in [MachineStatus.PENDING]:
                    pending_count += 1
                elif status in [MachineStatus.FAILED]:
                    failed_count += 1
                elif status in [MachineStatus.TERMINATED, MachineStatus.STOPPED]:
                    terminated_count += 1
                elif status == MachineStatus.SHUTTING_DOWN:
                    shutting_down_count += 1

            total_accounted = (
                running_count
                + pending_count
                + failed_count
                + terminated_count
                + shutting_down_count
            )

            self.logger.debug(
                "Machine status analysis for request %s (type: %s): "
                "requested=%d, provider_total=%d, running=%d, pending=%d, failed=%d, terminated=%d, shutting_down=%d",
                request.request_id,
                request_type.value,
                requested_count,
                provider_machine_count,
                running_count,
                pending_count,
                failed_count,
                terminated_count,
                shutting_down_count,
            )

            # Determine new status based on request type and machine states
            new_status = None
            status_message = None

            if request_type == RequestType.RETURN:
                # RETURN REQUEST LOGIC

                # Case 1: All machines terminated or no machines found (terminated long ago)
                if provider_machine_count == 0 or (
                    terminated_count > 0
                    and running_count == 0
                    and pending_count == 0
                    and shutting_down_count == 0
                ):
                    if current_status != RequestStatus.COMPLETED:
                        new_status = RequestStatus.COMPLETED.value
                        if provider_machine_count == 0:
                            status_message = f"Return request completed: all machines terminated (no longer visible in provider) (total in DB: {database_machine_count})"
                        else:
                            status_message = f"Return request completed: {terminated_count} machines terminated (total in DB: {database_machine_count})"

                # Case 2: Machines are shutting down (in progress)
                elif shutting_down_count > 0:
                    if current_status != RequestStatus.IN_PROGRESS:
                        new_status = RequestStatus.IN_PROGRESS.value
                        status_message = f"Return in progress: {shutting_down_count} machines shutting down, {terminated_count} terminated (total in DB: {database_machine_count})"

                # Case 3: Still have running machines (return not yet initiated or failed)
                elif running_count > 0:
                    if current_status not in [RequestStatus.IN_PROGRESS, RequestStatus.PENDING]:
                        new_status = RequestStatus.IN_PROGRESS.value
                        status_message = f"Return in progress: {running_count} machines still running, awaiting termination (total in DB: {database_machine_count})"

                # Case 4: Return request failed (machines failed to terminate properly)
                elif failed_count > 0 and running_count == 0 and shutting_down_count == 0:
                    if current_status != RequestStatus.FAILED:
                        new_status = RequestStatus.FAILED.value
                        status_message = f"Return request failed: {failed_count} machines failed to terminate properly (total in DB: {database_machine_count})"

            elif request_type == RequestType.ACQUIRE:
                # ACQUIRE REQUEST LOGIC (original logic)

                # Case 1: All requested machines are running successfully
                if running_count == requested_count and failed_count == 0:
                    if current_status != RequestStatus.COMPLETED:
                        new_status = RequestStatus.COMPLETED.value
                        status_message = f"All {requested_count} machines are running successfully (total in DB: {database_machine_count})"

                # Case 2: Some machines running, some failed (partial success)
                elif (
                    running_count > 0
                    and failed_count > 0
                    and (running_count + failed_count) >= requested_count
                ):
                    if current_status != RequestStatus.PARTIAL:
                        new_status = RequestStatus.PARTIAL.value
                        status_message = f"Partial success: {running_count}/{requested_count} machines running, {failed_count} failed (total in DB: {database_machine_count})"

                # Case 3: All machines failed
                elif failed_count >= requested_count and running_count == 0:
                    if current_status != RequestStatus.FAILED:
                        new_status = RequestStatus.FAILED.value
                        status_message = f"All {requested_count} machines failed to start (total in DB: {database_machine_count})"

                # Case 4: Still have pending machines (in progress)
                elif pending_count > 0 and failed_count < requested_count:
                    if current_status not in [RequestStatus.IN_PROGRESS, RequestStatus.PENDING]:
                        new_status = RequestStatus.IN_PROGRESS.value
                        status_message = f"Processing: {running_count} running, {pending_count} pending, {failed_count} failed (total in DB: {database_machine_count})"

                # Case 5: No machines found but request expects them (potential timeout or provider issue)
                elif provider_machine_count == 0 and requested_count > 0:
                    if current_status in [RequestStatus.IN_PROGRESS] and hasattr(
                        request, "created_at"
                    ):
                        # Check if request has been in progress too long (e.g., 30 minutes)
                        from datetime import datetime, timedelta

                        time_elapsed = datetime.utcnow() - request.created_at
                        if time_elapsed > timedelta(minutes=30):
                            new_status = RequestStatus.TIMEOUT.value
                            status_message = f"Request timed out - no machines found after 30 minutes (total in DB: {database_machine_count})"

            # Log the decision
            if new_status:
                self.logger.info(
                    "Request %s (%s) status change determined: %s -> %s (%s)",
                    request.request_id,
                    request_type.value,
                    current_status.value,
                    new_status,
                    status_message,
                )
            else:
                self.logger.debug(
                    "Request %s (%s) status remains unchanged: %s",
                    request.request_id,
                    request_type.value,
                    current_status.value,
                )

            return (new_status, status_message)

        except Exception as e:
            self.logger.error("Failed to determine request status from machines: %s", e)
            return (None, None)


@query_handler(GetRequestStatusQuery)
class GetRequestStatusQueryHandler(BaseQueryHandler[GetRequestStatusQuery, str]):
    """Handler for getting request status."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: GetRequestStatusQuery) -> str:
        """Execute get request status query."""
        self.logger.info("Getting status for request: %s", query.request_id)

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                # Convert string to RequestId value object
                from domain.request.value_objects import RequestId

                request_id = RequestId(value=query.request_id)
                request = uow.requests.get_by_id(request_id)
                if not request:
                    raise EntityNotFoundError("Request", query.request_id)

                status = request.status.value
                self.logger.info("Request %s status: %s", query.request_id, status)
                return status

        except EntityNotFoundError:
            self.logger.error("Request not found: %s", query.request_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get request status: %s", e)
            raise


@query_handler(ListActiveRequestsQuery)
class ListActiveRequestsHandler(BaseQueryHandler[ListActiveRequestsQuery, list[RequestDTO]]):
    """Handler for listing active requests."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: ListActiveRequestsQuery) -> list[RequestDTO]:
        """Execute list active requests query."""
        self.logger.info("Listing active requests")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                # Get active requests from repository
                from domain.request.value_objects import RequestStatus

                active_statuses = [
                    RequestStatus.PENDING,
                    RequestStatus.IN_PROGRESS,
                    RequestStatus.PROVISIONING,
                ]

                active_requests = uow.requests.find_by_statuses(active_statuses)

                # Convert to DTOs
                request_dtos = []
                for request in active_requests:
                    request_dto = RequestDTO(
                        request_id=str(request.request_id),
                        template_id=request.template_id,
                        requested_count=request.requested_count,
                        status=request.status.value,
                        created_at=request.created_at,
                        updated_at=request.updated_at,
                        metadata=request.metadata or {},
                    )
                    request_dtos.append(request_dto)

                self.logger.info("Found %s active requests", len(request_dtos))
                return request_dtos

        except Exception as e:
            self.logger.error("Failed to list active requests: %s", e)
            raise


@query_handler(ListReturnRequestsQuery)
class ListReturnRequestsHandler(BaseQueryHandler[ListReturnRequestsQuery, list[RequestDTO]]):
    """Handler for listing return requests."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: ListReturnRequestsQuery) -> list[RequestDTO]:
        """Execute list return requests query."""
        self.logger.info("Listing return requests")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                # Get return requests from repository
                from domain.request.value_objects import RequestType

                return_requests = uow.requests.find_by_type(RequestType.RETURN)

                # Convert to DTOs
                request_dtos = []
                for request in return_requests:
                    request_dto = RequestDTO(
                        request_id=str(request.request_id),
                        template_id=request.template_id,
                        requested_count=request.requested_count,
                        status=request.status.value,
                        created_at=request.created_at,
                        updated_at=request.updated_at,
                        metadata=request.metadata or {},
                    )
                    request_dtos.append(request_dto)

                self.logger.info("Found %s return requests", len(request_dtos))
                return request_dtos

        except Exception as e:
            self.logger.error("Failed to list return requests: %s", e)
            raise


@query_handler(GetTemplateQuery)
class GetTemplateHandler(BaseQueryHandler[GetTemplateQuery, Template]):
    """Handler for getting template details."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self._container = container

    async def execute_query(self, query: GetTemplateQuery) -> Template:
        """Execute get template query."""
        from domain.template.aggregate import Template
        from infrastructure.template.configuration_manager import (
            TemplateConfigurationManager,
        )

        self.logger.info("Getting template: %s", query.template_id)

        try:
            template_manager = self._container.get(TemplateConfigurationManager)

            # Get template by ID using the same approach as ListTemplatesHandler
            template_dto = await template_manager.get_template_by_id(query.template_id)

            if not template_dto:
                raise EntityNotFoundError("Template", query.template_id)

            # Convert TemplateDTO to Template domain object (same logic as
            # ListTemplatesHandler)
            config = template_dto.configuration or {}

            template_data = {
                "template_id": template_dto.template_id,
                "name": template_dto.name or template_dto.template_id,
                "provider_api": template_dto.provider_api or "aws",
                # Extract required fields from configuration with defaults
                "image_id": config.get("image_id") or config.get("imageId") or "default-image",
                "subnet_ids": config.get("subnet_ids")
                or config.get("subnetIds")
                or ["default-subnet"],
                "instance_type": config.get("instance_type") or config.get("instanceType"),
                "max_instances": config.get("max_instances") or config.get("maxNumber") or 1,
                "security_group_ids": config.get("security_group_ids")
                or config.get("securityGroupIds")
                or [],
                "tags": config.get("tags") or {},
                "metadata": config,
            }

            domain_template = Template(**template_data)

            self.logger.info("Retrieved template: %s", query.template_id)
            return domain_template

        except EntityNotFoundError:
            self.logger.error("Template not found: %s", query.template_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get template: %s", e)
            raise


@query_handler(ListTemplatesQuery)
class ListTemplatesHandler(BaseQueryHandler[ListTemplatesQuery, list[Template]]):
    """Handler for listing templates."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self._container = container

    async def execute_query(self, query: ListTemplatesQuery) -> list[Template]:
        """Execute list templates query."""
        from domain.template.aggregate import Template
        from infrastructure.template.configuration_manager import (
            TemplateConfigurationManager,
        )

        self.logger.info("Listing templates")

        try:
            template_manager = self._container.get(TemplateConfigurationManager)

            if query.provider_api:
                domain_templates = await template_manager.get_templates_by_provider(
                    query.provider_api
                )
            else:
                template_dtos = await template_manager.load_templates()
                # Convert TemplateDTO objects to Template domain objects
                domain_templates = []
                for dto in template_dtos:
                    try:
                        # Extract fields from configuration with defaults
                        config = dto.configuration or {}

                        # Create template with field mapping
                        template_data = {
                            "template_id": dto.template_id,
                            "name": dto.name or dto.template_id,
                            "provider_api": dto.provider_api or "aws",
                            # Extract required fields from configuration with defaults
                            "image_id": config.get("image_id")
                            or config.get("imageId")
                            or "default-image",
                            "subnet_ids": config.get("subnet_ids")
                            or config.get("subnetIds")
                            or ["default-subnet"],
                            "instance_type": config.get("instance_type")
                            or config.get("instanceType"),
                            "max_instances": config.get("max_instances")
                            or config.get("maxNumber")
                            or 1,
                            "security_group_ids": config.get("security_group_ids")
                            or config.get("securityGroupIds")
                            or [],
                            "tags": config.get("tags") or {},
                            "metadata": {},
                        }

                        domain_template = Template(**template_data)
                        domain_templates.append(domain_template)

                    except Exception as e:
                        self.logger.warning("Skipping invalid template %s: %s", dto.template_id, e)
                        continue

            self.logger.info("Found %s templates", len(domain_templates))
            return domain_templates

        except Exception as e:
            self.logger.error("Failed to list templates: %s", e)
            raise


@query_handler(ValidateTemplateQuery)
class ValidateTemplateHandler(BaseQueryHandler[ValidateTemplateQuery, ValidationDTO]):
    """Handler for validating template configuration."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: ValidateTemplateQuery) -> dict[str, Any]:
        """Execute validate template query."""
        self.logger.info("Validating template: %s", query.template_id)

        try:
            # Get template configuration port for validation
            from domain.base.ports.template_configuration_port import (
                TemplateConfigurationPort,
            )

            template_port = self.container.get(TemplateConfigurationPort)

            # Validate template configuration
            validation_errors = template_port.validate_template_config(query.configuration)

            # Log validation results
            if validation_errors:
                self.logger.warning(
                    "Template validation failed for %s: %s",
                    query.template_id,
                    validation_errors,
                )
            else:
                self.logger.info("Template validation passed for %s", query.template_id)

            return {
                "template_id": query.template_id,
                "is_valid": len(validation_errors) == 0,
                "validation_errors": validation_errors,
                "configuration": query.configuration,
            }

        except Exception as e:
            self.logger.error("Template validation failed for %s: %s", query.template_id, e)
            return {
                "template_id": query.template_id,
                "is_valid": False,
                "validation_errors": [f"Validation error: {e!s}"],
                "configuration": query.configuration,
            }


@query_handler(GetMachineQuery)
class GetMachineHandler(BaseQueryHandler[GetMachineQuery, MachineDTO]):
    """Handler for getting machine details."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: GetMachineQuery) -> MachineDTO:
        """Execute get machine query."""
        self.logger.info("Getting machine: %s", query.machine_id)

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                machine = uow.machines.get_by_id(query.machine_id)
                if not machine:
                    raise EntityNotFoundError("Machine", query.machine_id)

                # Convert to DTO
                machine_dto = MachineDTO(
                    machine_id=str(machine.machine_id),
                    provider_id=machine.provider_id,
                    template_id=machine.template_id,
                    request_id=str(machine.request_id) if machine.request_id else None,
                    status=machine.status.value,
                    instance_type=machine.instance_type,
                    created_at=machine.created_at,
                    updated_at=machine.updated_at,
                    metadata=machine.metadata or {},
                )

                self.logger.info("Retrieved machine: %s", query.machine_id)
                return machine_dto

        except EntityNotFoundError:
            self.logger.error("Machine not found: %s", query.machine_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get machine: %s", e)
            raise


@query_handler(ListMachinesQuery)
class ListMachinesHandler(BaseQueryHandler[ListMachinesQuery, list[MachineDTO]]):
    """Handler for listing machines."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: ListMachinesQuery) -> list[MachineDTO]:
        """Execute list machines query."""
        self.logger.info("Listing machines")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                # Get machines based on query filters
                if query.status_filter:
                    from domain.machine.value_objects import MachineStatus

                    status_enum = MachineStatus(query.status_filter)
                    machines = uow.machines.find_by_status(status_enum)
                elif query.request_id:
                    machines = uow.machines.find_by_request_id(query.request_id)
                else:
                    machines = uow.machines.get_all()

                # Convert to DTOs
                machine_dtos = []
                for machine in machines:
                    machine_dto = MachineDTO(
                        machine_id=str(machine.machine_id),
                        provider_id=machine.provider_id,
                        template_id=machine.template_id,
                        request_id=(str(machine.request_id) if machine.request_id else None),
                        status=machine.status.value,
                        instance_type=machine.instance_type,
                        created_at=machine.created_at,
                        updated_at=machine.updated_at,
                        metadata=machine.metadata or {},
                    )
                    machine_dtos.append(machine_dto)

                self.logger.info("Found %s machines", len(machine_dtos))
                return machine_dtos

        except Exception as e:
            self.logger.error("Failed to list machines: %s", e)
            raise
