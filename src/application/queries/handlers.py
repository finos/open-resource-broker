"""Query handlers for application services."""

from __future__ import annotations

# Import for type hints
from typing import TYPE_CHECKING, Any, TypeVar

from application.base.handlers import BaseQueryHandler
from application.decorators import query_handler
from application.dto.queries import (
    GetConfigurationQuery,
    GetMachineQuery,
    GetRequestQuery,
    GetTemplateQuery,
    ListActiveRequestsQuery,
    ListMachinesQuery,
    ListReturnRequestsQuery,
    ListTemplatesQuery,
    ValidateTemplateQuery,
)
from application.dto.responses import MachineDTO, RequestDTO
from application.dto.system import ValidationDTO
from application.request.queries import ListRequestsQuery
from application.services.provider_registry_service import ProviderRegistryService
from domain.base import UnitOfWorkFactory

# Exception handling through BaseQueryHandler (Clean Architecture compliant)
from domain.base.exceptions import EntityNotFoundError
from domain.base.ports import ContainerPort, ErrorHandlingPort, LoggingPort
from domain.services.generic_filter_service import GenericFilterService
from domain.services.timestamp_service import TimestampService

if TYPE_CHECKING:
    pass
from application.ports.command_bus_port import CommandBusPort
from application.ports.template_dto_port import TemplateDTOPort
from domain.template.factory import TemplateFactory, get_default_template_factory
from domain.template.template_aggregate import Template

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
        command_bus: CommandBusPort,
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
        from application.factories.request_dto_factory import RequestDTOFactory
        from application.services.machine_sync_service import MachineSyncService
        from application.services.request_query_service import RequestQueryService
        from application.services.request_status_service import RequestStatusService

        self._query_service = RequestQueryService(uow_factory, logger)
        self._status_service = RequestStatusService(uow_factory, logger)
        self._machine_sync_service = container.get(MachineSyncService)
        self._dto_factory = RequestDTOFactory()

    async def execute_query(self, query: GetRequestQuery) -> RequestDTO:
        """Execute get request query with command-driven sync."""
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

            # ✅ FIXED: Trigger sync command instead of doing writes in query
            from application.dto.commands import SyncRequestCommand
            from application.ports.command_bus_port import CommandBusPort

            try:
                command_bus = self._container.get(CommandBusPort)
                sync_command = SyncRequestCommand(request_id=query.request_id)
                await command_bus.execute(sync_command)
            except Exception as sync_err:
                self.logger.warning(
                    "Sync failed for request %s, returning stored state: %s",
                    query.request_id,
                    sync_err,
                )

            # Re-query after sync to get updated state
            request = await self._query_service.get_request(query.request_id)
            machine_objects = await self._query_service.get_machines_for_request(request)

            # Create RequestDTO using factory
            request_dto = self._dto_factory.create_from_domain(request, machine_objects)

            # Cache result if caching is enabled
            if self._cache_service and self._cache_service.is_caching_enabled():
                self._cache_service.cache_request(query.request_id, request_dto)

            self.logger.info("Retrieved request: %s", query.request_id)
            return request_dto

        except EntityNotFoundError:
            self.logger.error("Request not found: %s", query.request_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get request: %s", e)
            raise

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
                                machine_adapter = self._container.get_optional(  # type: ignore[arg-type]
                                    "providers.aws.infrastructure.adapters.machine_adapter.AWSMachineAdapter"  # type: ignore[arg-type]
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

            return updated_machines, provider_metadata

        except Exception as e:
            self.logger.warning("Failed to update machine status from AWS: %s", e)
            return machines, {}

        # Provider Registry methods removed - use Provider Registry directly instead
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

                Request.model_validate(
                    {
                        **request.model_dump(),
                        "metadata": updated_metadata,
                        "version": request.version + 1,
                    }
                )

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
            from providers.aws.infrastructure.adapters.aws_client import (
                AWSClient,  # type: ignore[import-untyped]
            )

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
    #     from domain.machine.machine_identifiers import MachineId
    #     from domain.machine.aggregate import Machine

    #     return Machine(
    #         instance_id=MachineId(value=aws_instance["InstanceId"]),
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
        from domain.machine.aggregate import Machine
        from domain.machine.machine_identifiers import MachineId

        # Detect format and normalize to snake_case for Pydantic
        if "instance_id" in aws_instance:
            # Already in snake_case format (from machine adapter)
            machine_data = dict(aws_instance)
            # Fix image_id from metadata if not present
            if machine_data.get("image_id") == "unknown" and "metadata" in machine_data:
                machine_data["image_id"] = machine_data["metadata"].get("ami_id", "unknown")
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
                "security_group_ids": [
                    sg["GroupId"] for sg in aws_instance.get("SecurityGroups", [])
                ],
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
                "resource_id": aws_instance.get("resource_id")
                or (request.resource_ids[0] if request.resource_ids else None),
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
            from application.ports.cache_service_port import CacheServicePort

            # Get cache service from container instead of creating it
            cache_service = self._container.get(CacheServicePort)
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


@query_handler(ListRequestsQuery)  # type: ignore[arg-type]
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
                if query.provider_name:
                    # Filter by provider name (check provider_api field)
                    requests = [
                        r
                        for r in requests
                        if r.provider_api and query.provider_name in r.provider_api
                    ]

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
                    filtered_dicts = self._generic_filter_service.apply_filters(
                        request_dicts, query.filter_expressions
                    )

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
                        metadata=request.metadata or {},
                    )
                    request_dtos.append(request_dto)

                # Apply generic filters if provided
                if query.filter_expressions:
                    # Convert RequestDTO objects to dicts for filtering
                    request_dicts = [dto.model_dump() for dto in request_dtos]

                    # Apply filters using GenericFilterService
                    filtered_dicts = self._generic_filter_service.apply_filters(
                        request_dicts, query.filter_expressions
                    )

                    # Convert back to RequestDTO objects
                    request_dtos = [RequestDTO.model_validate(d) for d in filtered_dicts]

                # Apply pagination
                total_count = len(request_dtos)
                limit = min(query.limit or 50, 1000)  # type: ignore[union-attr]  # Max 1000
                offset = query.offset or 0  # type: ignore[union-attr]
                request_dtos = request_dtos[offset : offset + limit]

                self.logger.info(
                    "Found %s return requests (total: %s, limit: %s, offset: %s)",
                    len(request_dtos),
                    total_count,
                    limit,
                    offset,
                )
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
        container: ContainerPort,
        command_bus: CommandBusPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._generic_filter_service = generic_filter_service
        self._container = container
        self.command_bus = command_bus

    async def execute_query(self, query: ListActiveRequestsQuery) -> list[RequestDTO]:
        """Execute list active requests query."""
        self.logger.info("Listing active requests")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                # Get initial requests
                if query.all_resources:
                    # Get ALL requests (not just active)
                    requests = uow.requests.find_all()
                else:
                    # Get only active requests
                    from domain.request.value_objects import RequestStatus

                    active_statuses = [
                        RequestStatus.PENDING,
                        RequestStatus.IN_PROGRESS,
                    ]
                    all_requests = uow.requests.find_all()
                    requests = [r for r in all_requests if r.status in active_statuses]

                    # Apply template filter if provided
                    if hasattr(query, "template_id") and query.template_id:  # type: ignore[union-attr]
                        requests = [r for r in requests if r.template_id == query.template_id]  # type: ignore[union-attr]

                # Store total count before pagination
                total_count = len(requests)

                # Apply pagination
                limit = min(query.limit or 50, 1000)  # type: ignore[union-attr]  # Max 1000
                offset = query.offset or 0  # type: ignore[union-attr]
                requests = requests[offset : offset + limit]

                # Sync each request with provider (like GetRequestHandler does)
                from application.dto.commands import SyncRequestCommand
                from application.services.machine_sync_service import MachineSyncService

                # Get machine sync service
                machine_sync_service = self._container.get(MachineSyncService)

                for request in requests:
                    try:
                        # Populate missing machine IDs first (like GetRequestHandler)
                        await machine_sync_service.populate_missing_machine_ids(request)

                        # Then sync with provider
                        sync_command = SyncRequestCommand(request_id=str(request.request_id.value))
                        await self.command_bus.execute(sync_command)
                    except Exception as e:
                        self.logger.warning(
                            "Failed to sync request %s: %s", request.request_id.value, e
                        )

            # Create new unit of work after sync to get fresh data
            with self.uow_factory.create_unit_of_work() as uow:
                # Re-query with same logic to get updated state
                if query.all_resources:
                    requests = uow.requests.find_all()
                else:
                    from domain.request.value_objects import RequestStatus

                    active_statuses = [
                        RequestStatus.PENDING,
                        RequestStatus.IN_PROGRESS,
                    ]
                    all_requests = uow.requests.find_all()
                    requests = [r for r in all_requests if r.status in active_statuses]

                    # Apply same filters again
                    if hasattr(query, "template_id") and query.template_id:  # type: ignore[union-attr]
                        requests = [r for r in requests if r.template_id == query.template_id]  # type: ignore[union-attr]

                    # Apply same pagination
                    start_idx = 0
                    end_idx = getattr(query, "limit", None) or 100
                    requests = requests[start_idx:end_idx]

                # Store total count before pagination
                total_count = len(requests)

                # Apply pagination
                limit = min(query.limit or 50, 1000)  # type: ignore[union-attr]  # Max 1000
                offset = query.offset or 0  # type: ignore[union-attr]
                requests = requests[offset : offset + limit]

                # Convert to DTOs
                request_dtos = []
                for request in requests:
                    # Query machines for this request using same method as GetRequestHandler
                    from domain.request.value_objects import RequestType

                    if request.request_type == RequestType.RETURN:
                        machines = uow.machines.find_by_return_request_id(
                            str(request.request_id.value)
                        )
                    else:
                        machines = uow.machines.find_by_request_id(str(request.request_id.value))

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
                    filtered_dicts = self._generic_filter_service.apply_filters(
                        request_dicts, query.filter_expressions
                    )

                    # Convert back to RequestDTO objects
                    request_dtos = [RequestDTO.model_validate(d) for d in filtered_dicts]

                self.logger.info(
                    "Found %s active requests (total: %s, limit: %s, offset: %s)",
                    len(request_dtos),
                    total_count,
                    limit,
                    offset,
                )
                return request_dtos

        except Exception as e:
            self.logger.error("Failed to list active requests: %s", e)
            raise


@query_handler(GetTemplateQuery)
class GetTemplateHandler(BaseQueryHandler[GetTemplateQuery, TemplateDTOPort]):
    """Handler for getting template details."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self._container = container

    async def execute_query(self, query: GetTemplateQuery) -> Template:  # type: ignore[override]
        """Execute get template query."""
        from domain.base.ports import TemplateConfigurationPort

        self.logger.info("Getting template: %s", query.template_id)

        try:
            template_manager = self._container.get(TemplateConfigurationPort)

            # Get template by ID using the same approach as ListTemplatesHandler
            template_dto = await template_manager.get_template_by_id(query.template_id)

            if not template_dto:
                raise EntityNotFoundError("Template", query.template_id)

            # Convert TemplateDTO to Template domain object
            template_data = template_dto.model_dump()
            template_data.setdefault("template_id", template_dto.template_id)
            template_data.setdefault("name", template_dto.name or template_dto.template_id)
            template_data.setdefault("provider_api", template_dto.provider_api or "aws")

            # Apply template defaults resolution
            from application.services.template_defaults_service import TemplateDefaultsService

            if self._container.has(TemplateDefaultsService):
                template_defaults_service = self._container.get(TemplateDefaultsService)
                resolved_data = template_defaults_service.resolve_template_defaults(
                    template_data,
                    provider_name=query.provider_name,  # type: ignore[call-arg]
                )
            else:
                resolved_data = template_data

            if self._container.has(TemplateFactory):
                template_factory = self._container.get(TemplateFactory)
            else:
                template_factory = get_default_template_factory()

            resolved_template = template_factory.create_template(resolved_data)

            self.logger.info("Retrieved template: %s", query.template_id)

            # Convert resolved domain template to DTO so defaults (e.g. fleet_role,
            # subnet_ids) applied by template_defaults_service are reflected in the
            # returned value.  Falling back to the original template_dto would silently
            # drop any field that was None in the raw DTO but filled in by defaults.
            from infrastructure.template.dtos import TemplateDTO

            return TemplateDTO.from_domain(resolved_template)  # type: ignore[return-value]

        except EntityNotFoundError:
            self.logger.error("Template not found: %s", query.template_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get template: %s", e)
            raise


@query_handler(ListTemplatesQuery)
class ListTemplatesHandler(BaseQueryHandler[ListTemplatesQuery, list[TemplateDTOPort]]):
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

    async def execute_query(self, query: ListTemplatesQuery) -> list[TemplateDTOPort]:
        """Execute list templates query - returns raw templates for scheduler formatting."""
        from domain.base.ports import TemplateConfigurationPort

        self.logger.info("Listing templates")

        try:
            template_manager = self._container.get(TemplateConfigurationPort)

            # Load templates with provider override if specified
            if query.provider_name:
                template_dtos = await template_manager.load_templates(
                    provider_override=query.provider_name
                )
            elif query.provider_api:
                template_dtos = await template_manager.get_templates_by_provider(query.provider_api)
            else:
                template_dtos = await template_manager.load_templates()

            # Apply generic filters if provided
            if query.filter_expressions:
                # Convert TemplateDTO objects to dicts for filtering
                template_dicts = [dto.model_dump() for dto in template_dtos]

                # Apply filters using GenericFilterService
                filtered_dicts = self._generic_filter_service.apply_filters(
                    template_dicts, query.filter_expressions
                )

                # Convert back to TemplateDTOPort objects
                # Note: This assumes the infrastructure provides a way to reconstruct DTOs
                template_dtos = filtered_dicts  # Return filtered dicts for now

            # Apply pagination
            total_count = len(template_dtos)
            limit = min(query.limit or 50, 1000)  # type: ignore[union-attr]  # Max 1000
            offset = query.offset or 0  # type: ignore[union-attr]

            self.logger.info(
                "Found %s templates (total: %s, limit: %s, offset: %s)",
                len(template_dtos),
                total_count,
                limit,
                offset,
            )
            return template_dtos  # type: ignore[return-value]

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

    async def execute_query(self, query: ValidateTemplateQuery) -> dict[str, Any]:  # type: ignore[override]
        """Execute validate template query."""
        template_config = query.template_config
        template_id = getattr(query, "template_id", None)

        # If template_id is provided in template_config, extract it
        if not template_id and isinstance(template_config, dict):
            template_id = template_config.get("template_id")

        # If we have a template_id but no actual config, load from storage
        if template_id and (not template_config or template_config == {"template_id": template_id}):
            self.logger.info("Loading template from storage: %s", template_id)
            try:
                from domain.base.ports import TemplateConfigurationPort

                template_manager = self.container.get(TemplateConfigurationPort)
                template_dto = await template_manager.get_template_by_id(template_id)

                if not template_dto:
                    from domain.base.exceptions import EntityNotFoundError

                    raise EntityNotFoundError("Template", template_id)

                template_config = template_dto.configuration or {}
                template_config["template_id"] = template_dto.template_id

            except Exception as e:
                self.logger.error("Failed to load template %s: %s", template_id, e)
                return {
                    "template_id": template_id,
                    "success": False,
                    "valid": False,
                    "message": f"Failed to load template: {e}",
                    "error": f"Failed to load template: {e}",
                }

        self.logger.info("Validating template: %s", template_id or "file-template")

        try:
            # Get template configuration port for validation
            from domain.base.ports.template_configuration_port import TemplateConfigurationPort

            template_port = self.container.get(TemplateConfigurationPort)

            # Validate template configuration
            validation_errors = template_port.validate_template_config(template_config)

            # Log validation results
            if validation_errors:
                self.logger.warning(
                    "Template validation failed for %s: %s",
                    template_id or "file-template",
                    validation_errors,
                )
            else:
                self.logger.info(
                    "Template validation passed for %s", template_id or "file-template"
                )

            success = len(validation_errors) == 0
            message = (
                "Template validation passed"
                if success
                else f"Template validation failed: {', '.join(validation_errors)}"
                if validation_errors
                else "Template validation failed"
            )

            return {
                "template_id": template_id or template_config.get("template_id", "file-template"),
                "success": success,
                "valid": success,
                "message": message,
                "validation_errors": validation_errors,
                "configuration": template_config,
            }

        except Exception as e:
            self.logger.error(
                "Template validation failed for %s: %s", template_id or "file-template", e
            )
            validation_errors = [f"Validation error: {e!s}"]
            return {
                "template_id": template_id or template_config.get("template_id", "file-template"),
                "success": False,
                "valid": False,
                "message": f"Template validation failed: {validation_errors[0]}",
                "validation_errors": validation_errors,
                "configuration": template_config,
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
                    request_id=machine.request_id,
                    metadata=machine.metadata or {},
                    # Additional fields needed by formatter
                    template_id=machine.template_id,
                    image_id=machine.image_id,
                    subnet_id=machine.subnet_id,
                    security_group_ids=machine.security_group_ids,
                    status_reason=machine.status_reason,
                    termination_time=machine.termination_time,
                    tags=machine.tags,
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
        container: ContainerPort,
        command_bus: CommandBusPort,
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

    async def execute_query(self, query: ListMachinesQuery) -> list[MachineDTO]:
        """Execute list machines query."""
        self.logger.info("Listing machines")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                if query.all_resources:
                    # Use repository to get ALL active machines
                    machines = uow.machines.find_active_machines()
                # Existing filtered logic
                elif query.status:
                    from domain.machine.value_objects import MachineStatus

                    status_enum = MachineStatus(query.status)
                    machines = uow.machines.find_by_status(status_enum)
                elif query.request_id:
                    machines = uow.machines.find_by_request_id(query.request_id)
                else:
                    machines = uow.machines.get_all()

                # Apply provider filtering if specified
                if query.provider_name:
                    machines = [
                        m
                        for m in machines
                        if m.provider_name and query.provider_name in m.provider_name
                    ]

                # Store total count before pagination
                total_count = len(machines)

                # Apply pagination
                limit = min(query.limit or 50, 1000)  # Max 1000
                offset = query.offset or 0
                machines = machines[offset : offset + limit]

                # Convert to DTOs (with sync for running machines)
                machine_dtos = []
                for machine in machines:
                    # For running machines, trigger a quick sync to get fresh name/DNS data
                    if machine.status.value == "running" and machine.request_id:
                        try:
                            # Get request and trigger sync
                            request = uow.requests.get_by_id(machine.request_id)
                            if request:
                                (
                                    provider_machines,
                                    _,
                                ) = await self._machine_sync_service.fetch_provider_machines(
                                    request, [machine]
                                )
                                if provider_machines:
                                    (
                                        synced_machines,
                                        _,
                                    ) = await self._machine_sync_service.sync_machines_with_provider(
                                        request, [machine], provider_machines
                                    )
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
                        price_type=machine.price_type,
                        result=MachineDTO._get_result_status(machine.status.value),
                        launch_time=self.timestamp_service.format_with_type(
                            machine.launch_time, query.timestamp_format or "auto"
                        ),
                        message=machine.status_reason or "",
                        provider_api=machine.provider_api,
                        provider_name=machine.provider_name,
                        provider_type=machine.provider_type,
                        resource_id=machine.resource_id,
                        request_id=machine.request_id,
                        return_request_id=machine.return_request_id,
                        metadata=machine.metadata or {},
                        subnet_id=machine.subnet_id,
                        security_group_ids=machine.security_group_ids or [],
                        template_id=machine.template_id,
                        image_id=machine.image_id,
                        status_reason=machine.status_reason,
                        termination_time=str(machine.termination_time)
                        if machine.termination_time is not None
                        else None,
                        tags=machine.tags,
                    )
                    machine_dtos.append(machine_dto.to_dict())

                # Apply generic filters if provided
                if query.filter_expressions:
                    # Apply filters using GenericFilterService (machine_dtos are already dicts)
                    machine_dtos = self._generic_filter_service.apply_filters(
                        machine_dtos, query.filter_expressions
                    )

                self.logger.info(
                    "Found %s machines (total: %s, limit: %s, offset: %s)",
                    len(machine_dtos),
                    total_count,
                    limit,
                    offset,
                )
                return machine_dtos  # type: ignore[return-value]

        except Exception as e:
            self.logger.error("Failed to list machines: %s", e)
            raise


@query_handler(GetConfigurationQuery)
class GetConfigurationHandler(BaseQueryHandler[GetConfigurationQuery, dict[str, Any]]):
    """Handler for getting configuration values."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize get configuration handler."""
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: GetConfigurationQuery) -> dict[str, Any]:
        """Execute get configuration query."""
        self.logger.info("Getting configuration value for key: %s", query.key)

        try:
            from domain.base.ports import ConfigurationPort

            config_manager = self.container.get(ConfigurationPort)
            value = config_manager.get_configuration_value(query.key, query.default)

            return {
                "key": query.key,
                "value": value,
                "default": query.default,
            }

        except Exception as e:
            self.logger.error("Failed to get configuration: %s", e)
            raise
