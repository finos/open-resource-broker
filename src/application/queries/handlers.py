"""Query handlers for application services."""

from __future__ import annotations

from typing import Any, TypeVar

from domain.services.timestamp_service import TimestampService
from domain.services.generic_filter_service import GenericFilterService

from application.base.handlers import BaseQueryHandler
from application.decorators import query_handler
from application.dto.queries import (
    GetMachineQuery,
    GetRequestQuery,
    GetTemplateQuery,
    ListActiveRequestsQuery,
    ListMachinesQuery,
    ListReturnRequestsQuery,
    ListTemplatesQuery,
    ValidateTemplateQuery,
)
from application.machine.queries import ListMachinesQuery as MachineListQuery
from application.request.queries import ListRequestsQuery
from application.dto.responses import MachineDTO, RequestDTO
from application.dto.system import ValidationDTO
from domain.base import UnitOfWorkFactory

# Exception handling through BaseQueryHandler (Clean Architecture compliant)
from domain.base.exceptions import EntityNotFoundError
from domain.base.ports import ContainerPort, ErrorHandlingPort, LoggingPort
from application.services.provider_registry_service import ProviderRegistryService

# Import for type hints
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    pass
from domain.template.factory import TemplateFactory, get_default_template_factory
from infrastructure.di.buses import CommandBus
from domain.template.template_aggregate import Template
from infrastructure.template.dtos import TemplateDTO

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
        command_bus: CommandBus,
        provider_registry_service: ProviderRegistryService,
    ) -> None:
        """Initialize the instance."""
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._container = container
        self.command_bus = command_bus
        self._provider_registry_service = provider_registry_service
        self._cache_service = self._get_cache_service()
        self.event_publisher = self._get_event_publisher()
        
        # Initialize services for SRP compliance
        from application.services.request_query_service import RequestQueryService
        from application.services.request_status_service import RequestStatusService
        from application.services.machine_sync_service import MachineSyncService
        from application.factories.request_dto_factory import RequestDTOFactory
        
        self._query_service = RequestQueryService(uow_factory, logger)
        self._status_service = RequestStatusService(uow_factory, logger)
        self._machine_sync_service = container.get(MachineSyncService)
        self._dto_factory = RequestDTOFactory()

    async def execute_query(self, query: GetRequestQuery) -> RequestDTO:
        """Execute get request query with command-driven population."""
        self.logger.info("Getting request details for: %s", query.request_id)

        try:
            # Check cache first if enabled
            if self._cache_service and self._cache_service.is_caching_enabled():
                cached_result = self._cache_service.get_cached_request(query.request_id)
                if cached_result:
                    self.logger.info("Cache hit for request: %s", query.request_id)
                    return cached_result

            # Get request from storage using query service
            request = await self._query_service.get_request(query.request_id)

            # Lightweight mode: return basic request data without machine fetching or provider sync
            if query.lightweight:
                request_dto = self._dto_factory.create_from_domain(request, [])
                self.logger.info("Retrieved lightweight request: %s", query.request_id)
                return request_dto

            # Trigger population command if needed (no direct writes in query)
            await self._machine_sync_service.populate_missing_machine_ids(request)
            
            # Re-query after population using query service
            request = await self._query_service.get_request(query.request_id)

            # Get machines using query service
            machine_obj_from_db = await self._query_service.get_machines_for_request(request)

            self.logger.debug(f"Machines associated with this request in DB: {machine_obj_from_db}")

            # Fetch machines from provider first
            machine_objects_from_provider, provider_metadata = await self._machine_sync_service.fetch_provider_machines(
                request, machine_obj_from_db
            )

            # Then sync/merge with DB machines
            machine_objects_from_provider, provider_metadata = await self._machine_sync_service.sync_machines_with_provider(
                request, machine_obj_from_db, machine_objects_from_provider
            )
            self.logger.debug(f"Machines from DB:  {machine_obj_from_db}")
            self.logger.debug(f"Machines from cloud provider:  {machine_objects_from_provider}")

            # Determine if request status needs updating based on machine states
            new_status, status_message = self._status_service.determine_status_from_machines(
                machine_obj_from_db, machine_objects_from_provider, request, provider_metadata
            )

            # Update request status if needed
            if new_status:
                updated_request = await self._status_service.update_request_status(
                    request, new_status, status_message
                )
                # Update the request object for DTO creation
                request = updated_request

            # Convert machines directly to DTOs - use provider machines if available, otherwise DB machines
            machines_for_response = machine_objects_from_provider if machine_objects_from_provider else machine_obj_from_db
            
            # Create RequestDTO using factory
            request_dto = self._dto_factory.create_from_domain(request, machines_for_response)

            # Cache the result if caching is enabled
            if self._cache_service and self._cache_service.is_caching_enabled():
                self._cache_service.cache_request(request_dto)

            self.logger.info(
                "Retrieved request with %s machines: %s",
                len(machines_for_response),
                query.request_id,
            )
            return request_dto

        except EntityNotFoundError:
            self.logger.error("Request not found: %s", query.request_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get request: %s", e)
            raise

    async def _fetch_provider_machines(self, request, existing_machines) -> tuple[list, dict]:
        """
        Fetch the latest machine list from the provider using Provider Registry.

        Uses machine_ids for return requests when available, prefers resource-based 
        discovery (DESCRIBE_RESOURCE_INSTANCES) for acquire requests to capture the
        full membership of the resource (ASG/Fleet). Falls back to GET_INSTANCE_STATUS
        when only instance IDs are known.
        """
        try:
            from providers.base.strategy import ProviderOperation, ProviderOperationType

            # Use machine_ids for return requests when available
            if request.request_type.value == "return" and request.machine_ids:
                operation_type = ProviderOperationType.GET_INSTANCE_STATUS
                parameters = {
                    "instance_ids": request.machine_ids,
                    "template_id": request.template_id,
                }
            # Prefer resource-level discovery for acquire requests
            elif request.resource_ids:
                operation_type = ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES
                parameters = {
                    "resource_ids": request.resource_ids,
                    "provider_api": request.metadata.get("provider_api", "RunInstances"),
                    "template_id": request.template_id,
                }
            else:
                # Fallback to instance-level discovery
                operation_type = ProviderOperationType.GET_INSTANCE_STATUS
                instance_ids = [m.machine_id.value for m in existing_machines]
                parameters = {
                    "instance_ids": instance_ids,
                    "template_id": request.template_id,
                }

            operation = ProviderOperation(
                operation_type=operation_type,
                parameters=parameters,
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
                },
            )

            # Get provider configuration
            from domain.base.ports.configuration_port import ConfigurationPort
            config_port = self._container.get(ConfigurationPort)
            provider_instance_config = config_port.get_provider_instance_config(request.provider_name)
            
            # Execute operation using Provider Registry Service
            # Pass the full ProviderInstanceConfig object, not just the nested config dict
            result = await self._provider_registry_service.execute_operation(request.provider_name, operation)

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
                return [], result.metadata or {}

            # Get instance details from result
            instance_details = result.data.get("instances", []) or result.data.get("machines", [])
            if hasattr(instance_details, "__await__"):
                self.logger.debug("Provider returned awaitable instances result, awaiting it")
                instance_details = await instance_details
            if not instance_details:
                self.logger.info("No instances found for request %s", request.request_id)
                return [], result.metadata or {}

            # Create machine aggregates from instance details
            machines = []
            for instance_data in instance_details:
                self.logger.debug("instance_data: %s", instance_data)
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

            return machines, result.metadata or {}

        except Exception as e:
            self.logger.exception(
                "Failed to fetch provider machines: %s", e, exc_info=True
            )
            return [], {}

    def _normalize_provider_entry(self, entry: Any) -> dict[str, Any]:
        """Normalize provider entry (dict or DomainMachine) to a common shape.

        Example (AWS-like dict -> normalized):
            input:  {"InstanceId": "i-123", "State": "running", "PrivateIpAddress": "10.0.0.5"}
            output: {"instance_id": "i-123", "status": MachineStatus.RUNNING, "private_ip": "10.0.0.5"}
        """
        from domain.machine.aggregate import Machine as DomainMachine
        from domain.machine.machine_status import MachineStatus

        if isinstance(entry, DomainMachine):
            status_val = entry.status
            try:
                status_obj = (
                    status_val
                    if isinstance(status_val, MachineStatus)
                    else MachineStatus.from_str(str(status_val))
                )
            except Exception:
                status_obj = MachineStatus.UNKNOWN
            return {
                "instance_id": str(entry.machine_id.value),
                "status": status_obj,
                "private_ip": entry.private_ip,
                "public_ip": entry.public_ip,
                "launch_time": entry.launch_time,
                "instance_type": getattr(entry.instance_type, "value", entry.instance_type),
                "image_id": entry.image_id,
                "subnet_id": entry.subnet_id,
                "metadata": entry.metadata or {},
                "raw": entry,
            }
        if isinstance(entry, dict):
            status_val = entry.get("status") or entry.get("State") or entry.get("state")
            try:
                status_obj = (
                    status_val
                    if isinstance(status_val, MachineStatus)
                    else MachineStatus.from_str(str(status_val))
                    if status_val
                    else MachineStatus.UNKNOWN
                )
            except Exception:
                status_obj = MachineStatus.UNKNOWN
            return {
                "instance_id": entry.get("instance_id") or entry.get("InstanceId"),
                "status": status_obj,
                "private_ip": entry.get("private_ip") or entry.get("PrivateIpAddress"),
                "public_ip": entry.get("public_ip") or entry.get("PublicIpAddress"),
                "launch_time": entry.get("launch_time") or entry.get("LaunchTime"),
                "instance_type": entry.get("instance_type") or entry.get("InstanceType"),
                "image_id": entry.get("image_id") or entry.get("ImageId"),
                "subnet_id": entry.get("subnet_id") or entry.get("SubnetId"),
                "metadata": entry.get("metadata") or entry,
                "raw": entry,
            }
        return {}

    async def _update_machine_status_from_aws(
        self,
        machines: list,
        request=None,
        provider_machine_entities: list | None = None,
        provider_metadata: dict | None = None,
    ) -> tuple[list, dict]:
        """Merge provider machine view into storage.

        If the provider reports instances we don't have locally, create them so the
        status decision logic can see the full picture. Provider calls should be made
        by the caller; this function only merges/updates/persists.

        Stages:
            1. Resolve request context and normalize inputs.
            2. Prepare lookup maps and domain helpers.
            3. Reconcile provider view with local machines (compute updates/creates).
            4. Persist changes, finalize merged machine list, update metadata, and return.

        Returns:
            tuple[list, dict]: (updated_machines, provider_metadata) where the first
            item is the merged machine list and the second is passthrough provider metadata.
        """
        try:
            # <1.> Resolve request context and normalize inputs.
            # Group machines by request to use existing check_hosts_status methods
            if not machines:
                machines = []

            # Get the request for the first machine (all should be same request)
            if not request:
                request_id = str(machines[0].request_id)
                with self.uow_factory.create_unit_of_work() as uow:
                    from domain.request.value_objects import RequestId

                    request = uow.requests.get_by_id(RequestId(value=request_id))
                    if not request:
                        return machines, {}

            # provider_machine_entities now contains the authoritative provider view
            domain_machines = provider_machine_entities or []
            provider_metadata = provider_metadata or {}

            # <2.> Prepare lookup maps and domain helpers.
            # Ensure we have a map of existing machines for quick lookup
            existing_by_id = {str(m.machine_id.value): m for m in machines}
            updated_machines = []
            new_machines = []
            to_upsert = []

            from domain.machine.aggregate import Machine as DomainMachine
            from domain.machine.machine_status import MachineStatus

            # <3.> Reconcile provider view with local machines (compute updates/creates).
            # Update existing machines and add new ones discovered from provider
            for dm in domain_machines:
                normalized = self._normalize_provider_entry(dm)
                dm_id = normalized.get("instance_id")
                if not dm_id:
                    continue

                existing = existing_by_id.get(dm_id)

                if existing:
                    new_status = normalized.get("status") or MachineStatus.UNKNOWN

                    # Do we need to update this machine in DB?
                    needs_update = (
                        existing.status != new_status
                        or existing.private_ip != normalized.get("private_ip")
                        or existing.public_ip != normalized.get("public_ip")
                    )

                    if needs_update:
                        machine_data = existing.model_dump()
                        machine_data["status"] = new_status
                        machine_data["private_ip"] = normalized.get("private_ip")
                        machine_data["public_ip"] = normalized.get("public_ip")
                        machine_data["launch_time"] = normalized.get(
                            "launch_time", existing.launch_time
                        )
                        machine_data["version"] = existing.version + 1

                        updated_machine = DomainMachine.model_validate(machine_data)
                        to_upsert.append(updated_machine)
                        updated_machines.append(updated_machine)
                    else:
                        updated_machines.append(existing)
                else:
                    # New machine discovered from provider
                    try:
                        # If the provider already returned a domain machine, reuse it
                        if isinstance(dm, DomainMachine):
                            created_machine = dm
                        else:
                            if self._container:
                                from infrastructure.di.container import get_container

                                container = get_container()
                                machine_adapter = container.get_optional(
                                    "providers.aws.infrastructure.adapters.machine_adapter.AWSMachineAdapter"
                                )
                            else:
                                machine_adapter = None

                            if machine_adapter:
                                created_machine = machine_adapter.create_machine_from_aws_instance(
                                    normalized.get("raw"),
                                    request_id=str(request.request_id),
                                    provider_api=request.metadata.get("provider_api", "ASG"),
                                    resource_id=request.resource_ids[0]
                                    if request.resource_ids
                                    else None,
                                )
                            else:
                                # Fallback to building minimal Machine
                                created_machine = DomainMachine(
                                    machine_id=dm_id,
                                    template_id=request.template_id,
                                    request_id=str(request.request_id),
                                    provider_type=request.provider_type,
                                    status=normalized.get("status", MachineStatus.PENDING),
                                    instance_type=normalized.get("instance_type"),
                                    image_id=normalized.get("image_id"),
                                    private_ip=normalized.get("private_ip"),
                                    public_ip=normalized.get("public_ip"),
                                    subnet_id=normalized.get("subnet_id"),
                                    metadata=normalized.get("metadata", {}),
                                )

                        to_upsert.append(created_machine)
                        new_machines.append(created_machine)
                    except Exception as exc:
                        self.logger.warning(
                            "Failed to create machine %s from provider data: %s", dm_id, exc
                        )

            # <4.> Persist changes, finalize merged machine list, update metadata, and return.
            if to_upsert:
                with self.uow_factory.create_unit_of_work() as uow:
                    batch_events = uow.machines.save_batch(to_upsert)
                    for event in batch_events:
                        self.event_publisher.publish(event)

            # Merge existing updates and newly discovered machines
            if new_machines:
                updated_machines.extend(new_machines)

            # Ensure any original machines not updated are retained
            seen_ids = {str(m.machine_id.value) for m in updated_machines}
            for m in machines:
                if str(m.machine_id.value) not in seen_ids:
                    updated_machines.append(m)

            # Update ASG metadata if this is an ASG request
            if request.metadata.get("provider_api") == "ASG":
                await self._update_asg_metadata_if_needed(request, updated_machines)

            return updated_machines, provider_metadata

        except Exception as e:
            self.logger.warning("Failed to update machine status from AWS: %s", e)
            return machines, {}

    async def _update_asg_metadata_if_needed(self, request, machines):
        """Update ASG-specific metadata when capacity changes are detected."""
        try:
            from datetime import datetime

            # Get current ASG details from AWS if we have resource IDs
            if not request.resource_ids:
                return

            asg_name = request.resource_ids[0]  # ASG name is the resource_id
            current_asg_details = await self._get_current_asg_details(asg_name)

            if not current_asg_details:
                return

            # Compare with stored metadata
            stored_capacity = request.metadata.get("asg_desired_capacity")
            current_capacity = current_asg_details.get("DesiredCapacity")
            current_instances = len(
                [m for m in machines if m.status.value in ["running", "pending"]]
            )

            # Check if capacity has changed or if this is the first time we're tracking it
            capacity_changed = stored_capacity != current_capacity
            first_time_tracking = stored_capacity is None

            if capacity_changed or first_time_tracking:
                # Update request metadata with new ASG capacity
                updated_metadata = request.metadata.copy()
                updated_metadata.update(
                    {
                        "asg_desired_capacity": current_capacity,
                        "asg_min_size": current_asg_details.get("MinSize", 0),
                        "asg_max_size": current_asg_details.get("MaxSize", current_capacity * 2),
                        "asg_current_instances": current_instances,
                        "asg_capacity_last_updated": datetime.utcnow().isoformat(),
                        "asg_capacity_change_detected": capacity_changed,
                    }
                )

                # If this is the first time, also set creation metadata
                if first_time_tracking:
                    updated_metadata.update(
                        {
                            "asg_name": asg_name,
                            "asg_capacity_created_at": datetime.utcnow().isoformat(),
                            "asg_initial_capacity": current_capacity,
                        }
                    )

                # Update request with new metadata
                from domain.request.aggregate import Request

                updated_request = Request.model_validate(
                    {
                        **request.model_dump(),
                        "metadata": updated_metadata,
                        "version": request.version + 1,
                    }
                )

                # Save to database
                with self.uow_factory.create_unit_of_work() as uow:
                    uow.requests.save(updated_request)

                action = "Initialized" if first_time_tracking else "Updated"
                self.logger.info(
                    "%s ASG capacity metadata for request %s: %s -> %s (instances: %s)",
                    action,
                    request.request_id,
                    stored_capacity,
                    current_capacity,
                    current_instances,
                )

        except Exception as e:
            self.logger.warning("Failed to update ASG metadata: %s", e)

    async def _get_current_asg_details(self, asg_name: str) -> dict:
        """Get current ASG details from AWS."""
        try:
            # Create a simple AWS client call to get ASG details
            # This is a simplified approach - in production you might want to use
            # the provider context more directly
            from providers.aws.infrastructure.adapters.aws_client import AWSClient

            aws_client = self._container.get(AWSClient)

            response = aws_client.autoscaling_client.describe_auto_scaling_groups(
                AutoScalingGroupNames=[asg_name]
            )

            if response.get("AutoScalingGroups"):
                return response["AutoScalingGroups"][0]
            else:
                self.logger.warning("ASG %s not found", asg_name)
                return {}

        except Exception as e:
            self.logger.warning("Failed to get ASG details for %s: %s", asg_name, e)
            return {}

    # Provider Registry methods removed - use Provider Registry directly instead



    # def _create_machine_from_aws_data(self, aws_instance: dict[str, Any], request):
    #     """Create machine aggregate from AWS instance data."""
    #     from domain.base.value_objects import InstanceId
    #     from domain.machine.aggregate import Machine

    #     return Machine(
    #         instance_id=InstanceId(value=aws_instance["InstanceId"]),
    #         request_id=str(request.request_id),
    #         # Use first for backward compatibility
    #         resource_id=request.resource_ids[0] if request.resource_ids else None,
    #         template_id=request.template_id,
    #         provider_type="aws",
    #         status=self._map_aws_state_to_machine_status(aws_instance["State"]),
    #         private_ip=aws_instance.get("PrivateIpAddress"),
    #         public_ip=aws_instance.get("PublicIpAddress"),
    #         launch_time=aws_instance.get("LaunchTime"),
    #     )

    def _create_machine_from_aws_data(self, aws_instance: dict[str, Any], request):
        """Create machine aggregate using Pydantic validation with format detection."""
        from domain.machine.machine_identifiers import MachineId
        from domain.machine.aggregate import Machine

        # Detect format and normalize to snake_case for Pydantic
        if "instance_id" in aws_instance:
            # Already in snake_case format (from machine adapter)
            machine_data = dict(aws_instance)
        else:
            # PascalCase format (from provider strategy) - convert to snake_case
            machine_data = {
                "instance_id": aws_instance.get("InstanceId"),
                "status": aws_instance.get("State", {}).get("Name")
                if isinstance(aws_instance.get("State"), dict)
                else aws_instance.get("State"),
                "instance_type": aws_instance.get("InstanceType"),
                "image_id": aws_instance.get("ImageId", "unknown"),
                "private_ip": aws_instance.get("PrivateIpAddress"),
                "public_ip": aws_instance.get("PublicIpAddress"),
                "launch_time": aws_instance.get("LaunchTime"),
                "subnet_id": aws_instance.get("SubnetId"),
                "security_group_ids": aws_instance.get("SecurityGroups", []),
                "tags": {
                    "tags": {
                        tag.get("Key", ""): tag.get("Value", "")
                        for tag in aws_instance.get("Tags", [])
                    }
                },
                "metadata": aws_instance,  # Store original data as metadata
            }

        # Add required context fields
        machine_data.update(
            {
                "request_id": str(request.request_id),
                "template_id": request.template_id,
                "provider_type": request.provider_type,
                "provider_name": request.provider_name,
                "provider_api": request.metadata.get("provider_api") or request.provider_api,
                "resource_id": aws_instance.get("resource_id") or (request.resource_ids[0] if request.resource_ids else None),
            }
        )

        # Validate required fields before Pydantic validation
        if not machine_data.get("instance_id"):
            raise ValueError("Missing instance_id in AWS instance data")
        if not machine_data.get("instance_type"):
            raise ValueError("Missing instance_type in AWS instance data")
        if not machine_data.get("image_id"):
            machine_data["image_id"] = "unknown"  # Provide default

        # Create value objects explicitly for Pydantic
        from domain.base.value_objects import InstanceType

        # Convert strings to proper value objects
        machine_data["machine_id"] = MachineId(value=machine_data["instance_id"])
        del machine_data["instance_id"]  # Remove old field name
        machine_data["instance_type"] = InstanceType(value=machine_data["instance_type"])

        # Let Pydantic handle validation, type conversion, and field mapping
        return Machine.model_validate(machine_data)

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


@query_handler(ListRequestsQuery)
class ListRequestsHandler(BaseQueryHandler[ListRequestsQuery, list[RequestDTO]]):
    """Handler for listing requests with filtering."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        generic_filter_service: GenericFilterService,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._generic_filter_service = generic_filter_service

    async def execute_query(self, query: ListRequestsQuery) -> list[RequestDTO]:
        """Execute list requests query."""
        self.logger.info("Listing requests with filters")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                # Get requests from repository with filters
                requests = uow.requests.find_all()

                # Apply filters if provided
                if query.status:
                    from domain.request.value_objects import RequestStatus
                    status_filter = RequestStatus(query.status)
                    requests = [r for r in requests if r.status == status_filter]

                if query.template_id:
                    requests = [r for r in requests if r.template_id == query.template_id]

                # Apply pagination
                total_count = len(requests)
                start_idx = query.offset or 0
                end_idx = start_idx + (query.limit or 50)
                requests = requests[start_idx:end_idx]

                # Convert to DTOs using standard from_domain method with machine JOIN
                request_dtos = []
                for request in requests:
                    # Query machines for this request if machine_ids exist
                    machines = []
                    if request.machine_ids:
                        machines = uow.machines.find_by_ids(request.machine_ids)
                    
                    # Create RequestDTO using factory
                    from application.factories.request_dto_factory import RequestDTOFactory
                    dto_factory = RequestDTOFactory()
                    request_dto = dto_factory.create_from_domain(request, machines)
                    request_dtos.append(request_dto)

                # Apply generic filters if provided
                if query.filter_expressions:
                    # Convert RequestDTO objects to dicts for filtering
                    request_dicts = [dto.model_dump() for dto in request_dtos]
                    
                    # Apply filters using GenericFilterService
                    filtered_dicts = self._generic_filter_service.apply_filters(request_dicts, query.filter_expressions)
                    
                    # Convert back to RequestDTO objects
                    request_dtos = [RequestDTO.model_validate(d) for d in filtered_dicts]

                self.logger.info("Found %s requests (total: %s)", len(request_dtos), total_count)
                return request_dtos

        except Exception as e:
            self.logger.error("Failed to list requests: %s", e)
            raise


@query_handler(ListReturnRequestsQuery)
class ListReturnRequestsHandler(BaseQueryHandler[ListReturnRequestsQuery, list[RequestDTO]]):
    """Handler for listing return requests."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        generic_filter_service: GenericFilterService,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._generic_filter_service = generic_filter_service

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

                # Apply generic filters if provided
                if query.filter_expressions:
                    # Convert RequestDTO objects to dicts for filtering
                    request_dicts = [dto.model_dump() for dto in request_dtos]
                    
                    # Apply filters using GenericFilterService
                    filtered_dicts = self._generic_filter_service.apply_filters(request_dicts, query.filter_expressions)
                    
                    # Convert back to RequestDTO objects
                    request_dtos = [RequestDTO.model_validate(d) for d in filtered_dicts]

                self.logger.info("Found %s return requests", len(request_dtos))
                return request_dtos

        except Exception as e:
            self.logger.error("Failed to list return requests: %s", e)
            raise


@query_handler(ListActiveRequestsQuery)
class ListActiveRequestsHandler(BaseQueryHandler[ListActiveRequestsQuery, list[RequestDTO]]):
    """Handler for listing active requests."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        generic_filter_service: GenericFilterService,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._generic_filter_service = generic_filter_service

    async def execute_query(self, query: ListActiveRequestsQuery) -> list[RequestDTO]:
        """Execute list active requests query."""
        self.logger.info("Listing active requests")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                # Get active requests from repository
                from domain.request.value_objects import RequestStatus
                
                active_statuses = [RequestStatus.PENDING, RequestStatus.RUNNING, RequestStatus.PROVISIONING]
                requests = uow.requests.find_all()
                active_requests = [r for r in requests if r.status in active_statuses]

                # Apply template filter if provided
                if query.template_id:
                    active_requests = [r for r in active_requests if r.template_id == query.template_id]

                # Apply pagination
                total_count = len(active_requests)
                start_idx = 0
                end_idx = query.limit or 100
                active_requests = active_requests[start_idx:end_idx]

                # Convert to DTOs
                request_dtos = []
                for request in active_requests:
                    # Query machines for this request if machine_ids exist
                    machines = []
                    if request.machine_ids:
                        machines = uow.machines.find_by_ids(request.machine_ids)
                    
                    # Create RequestDTO using factory
                    from application.factories.request_dto_factory import RequestDTOFactory
                    dto_factory = RequestDTOFactory()
                    request_dto = dto_factory.create_from_domain(request, machines)
                    request_dtos.append(request_dto)

                # Apply generic filters if provided
                if query.filter_expressions:
                    # Convert RequestDTO objects to dicts for filtering
                    request_dicts = [dto.model_dump() for dto in request_dtos]
                    
                    # Apply filters using GenericFilterService
                    filtered_dicts = self._generic_filter_service.apply_filters(request_dicts, query.filter_expressions)
                    
                    # Convert back to RequestDTO objects
                    request_dtos = [RequestDTO.model_validate(d) for d in filtered_dicts]

                self.logger.info("Found %s active requests (total: %s)", len(request_dtos), total_count)
                return request_dtos

        except Exception as e:
            self.logger.error("Failed to list active requests: %s", e)
            raise


@query_handler(GetTemplateQuery)
class GetTemplateHandler(BaseQueryHandler[GetTemplateQuery, TemplateDTO]):
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
        from infrastructure.template.configuration_manager import TemplateConfigurationManager

        self.logger.info("Getting template: %s", query.template_id)

        try:
            template_manager = self._container.get(TemplateConfigurationManager)

            # Get template by ID using the same approach as ListTemplatesHandler
            template_dto = await template_manager.get_template_by_id(query.template_id)

            if not template_dto:
                raise EntityNotFoundError("Template", query.template_id)

            # Convert TemplateDTO to Template domain object
            config = dict(template_dto.configuration or {})
            template_data = dict(config)
            template_data.setdefault("template_id", template_dto.template_id)
            template_data.setdefault("name", template_dto.name or template_dto.template_id)
            template_data.setdefault("provider_api", template_dto.provider_api or "aws")

            # Apply template defaults resolution
            from application.services.template_defaults_service import TemplateDefaultsService
            if self._container.has(TemplateDefaultsService):
                template_defaults_service = self._container.get(TemplateDefaultsService)
                resolved_data = template_defaults_service.resolve_template_defaults(
                    template_data, provider_name=query.provider_name
                )
            else:
                resolved_data = template_data

            if self._container.has(TemplateFactory):
                template_factory = self._container.get(TemplateFactory)
            else:
                template_factory = get_default_template_factory()

            domain_template = template_factory.create_template(resolved_data)

            self.logger.info("Retrieved template: %s", query.template_id)
            
            # Convert domain template to DTO for CQRS compliance
            return TemplateDTO.from_domain(domain_template)

        except EntityNotFoundError:
            self.logger.error("Template not found: %s", query.template_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get template: %s", e)
            raise


@query_handler(ListTemplatesQuery)
class ListTemplatesHandler(BaseQueryHandler[ListTemplatesQuery, list[TemplateDTO]]):
    """Handler for listing templates."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
        generic_filter_service: GenericFilterService,
    ) -> None:
        super().__init__(logger, error_handler)
        self._container = container
        self._generic_filter_service = generic_filter_service

    async def execute_query(self, query: ListTemplatesQuery) -> list[TemplateDTO]:
        """Execute list templates query - returns raw templates for scheduler formatting."""
        from infrastructure.template.configuration_manager import TemplateConfigurationManager

        self.logger.info("Listing templates")

        try:
            template_manager = self._container.get(TemplateConfigurationManager)

            # Load templates with provider override if specified
            if query.provider_name:
                template_dtos = await template_manager.load_templates(provider_override=query.provider_name)
            elif query.provider_api:
                template_dtos = await template_manager.get_templates_by_provider(query.provider_api)
            else:
                template_dtos = await template_manager.load_templates()



            # Apply generic filters if provided
            if query.filter_expressions:
                # Convert TemplateDTO objects to dicts for filtering
                template_dicts = [dto.model_dump() for dto in template_dtos]
                
                # Apply filters using GenericFilterService
                filtered_dicts = self._generic_filter_service.apply_filters(template_dicts, query.filter_expressions)
                
                # Convert back to TemplateDTO objects
                template_dtos = [TemplateDTO.model_validate(d) for d in filtered_dicts]

            self.logger.info("Found %s templates", len(template_dtos))
            return template_dtos

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
            from domain.base.ports.template_configuration_port import TemplateConfigurationPort

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
        timestamp_service: TimestampService,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self.timestamp_service = timestamp_service

    async def execute_query(self, query: GetMachineQuery) -> MachineDTO:
        """Execute get machine query."""
        self.logger.info("Getting machine: %s", query.machine_id)

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                machine = uow.machines.get_by_id(query.machine_id)
                if not machine:
                    raise EntityNotFoundError("Machine", query.machine_id)

                # Convert to DTO with available fields
                machine_dto = MachineDTO(
                    machine_id=str(machine.machine_id),
                    name=machine.name or str(machine.machine_id),  # Fallback to machine_id
                    status=machine.status.value,
                    instance_type=str(machine.instance_type),
                    private_ip=machine.private_ip or "unknown",
                    public_ip=machine.public_ip,
                    private_dns_name=machine.private_dns_name,
                    public_dns_name=machine.public_dns_name,
                    result=MachineDTO._get_result_status(machine.status.value),
                    launch_time=self.timestamp_service.format_for_dto(machine.launch_time),
                    message=machine.status_reason or "",
                    provider_api=machine.provider_api,
                    provider_name=machine.provider_name,
                    provider_type=machine.provider_type,
                    resource_id=machine.resource_id,
                    metadata=machine.metadata or {},
                )

                self.logger.info("Retrieved machine: %s", query.machine_id)
                return machine_dto.to_dict()

        except EntityNotFoundError:
            self.logger.error("Machine not found: %s", query.machine_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get machine: %s", e)
            raise


@query_handler(MachineListQuery)
class ListMachinesHandler(BaseQueryHandler[MachineListQuery, list[MachineDTO]]):
    """Handler for listing machines."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
        command_bus: CommandBus,
        timestamp_service: TimestampService,
        generic_filter_service: GenericFilterService,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self.container = container
        self.command_bus = command_bus
        self.timestamp_service = timestamp_service
        self._generic_filter_service = generic_filter_service
        
        # Initialize machine sync service via DI
        from application.services.machine_sync_service import MachineSyncService
        self._machine_sync_service = container.get(MachineSyncService)

    async def execute_query(self, query: MachineListQuery) -> list[MachineDTO]:
        """Execute list machines query."""
        self.logger.info("Listing machines")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                # Get machines based on query filters
                if query.status:
                    from domain.machine.value_objects import MachineStatus

                    status_enum = MachineStatus(query.status)
                    machines = uow.machines.find_by_status(status_enum)
                elif query.request_id:
                    machines = uow.machines.find_by_request_id(query.request_id)
                else:
                    machines = uow.machines.get_all()

                # Convert to DTOs (with sync for running machines)
                machine_dtos = []
                for machine in machines:
                    # For running machines, trigger a quick sync to get fresh name/DNS data
                    if machine.status.value == "running" and machine.request_id:
                        try:
                            # Get request and trigger sync
                            request = uow.requests.get_by_id(machine.request_id)
                            if request:
                                provider_machines, _ = await self._machine_sync_service.fetch_provider_machines(request, [machine])
                                if provider_machines:
                                    synced_machines, _ = await self._machine_sync_service.sync_machines_with_provider(request, [machine], provider_machines)
                                    if synced_machines:
                                        machine = synced_machines[0]  # Use synced data
                        except Exception as e:
                            self.logger.debug(f"Sync failed for machine {machine.machine_id}: {e}")
                    
                    machine_dto = MachineDTO(
                        machine_id=str(machine.machine_id),
                        name=machine.name or str(machine.machine_id),  # Fallback to machine_id
                        status=machine.status.value,
                        instance_type=str(machine.instance_type),
                        private_ip=machine.private_ip or "unknown",
                        public_ip=machine.public_ip,
                        private_dns_name=machine.private_dns_name,
                        public_dns_name=machine.public_dns_name,
                        result=MachineDTO._get_result_status(machine.status.value),
                        launch_time=self.timestamp_service.format_with_type(machine.launch_time, query.timestamp_format or "auto"),
                        message=machine.status_reason or "",
                        provider_api=machine.provider_api,
                        provider_name=machine.provider_name,
                        provider_type=machine.provider_type,
                        resource_id=machine.resource_id,
                        metadata=machine.metadata or {},
                    )
                    machine_dtos.append(machine_dto.to_dict())

                # Apply pagination
                total_count = len(machine_dtos)
                start_idx = query.offset or 0
                end_idx = start_idx + (query.limit or 50)
                machine_dtos = machine_dtos[start_idx:end_idx]

                # Apply generic filters if provided
                if query.filter_expressions:
                    # Apply filters using GenericFilterService (machine_dtos are already dicts)
                    machine_dtos = self._generic_filter_service.apply_filters(machine_dtos, query.filter_expressions)

                self.logger.info("Found %s machines (total: %s)", len(machine_dtos), total_count)
                return machine_dtos

        except Exception as e:
            self.logger.error("Failed to list machines: %s", e)
            raise
