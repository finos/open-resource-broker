"""Query handlers for application services."""

from __future__ import annotations

from typing import Any, Dict, List, TypeVar

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
        self.logger.info(f"Getting request details for: {query.request_id}")

        try:
            # Check cache first if enabled
            if self._cache_service and self._cache_service.is_caching_enabled():
                cached_result = self._cache_service.get_cached_request(query.request_id)
                if cached_result:
                    self.logger.info(f"Cache hit for request: {query.request_id}")
                    return cached_result

            # Cache miss - get request from storage
            with self.uow_factory.create_unit_of_work() as uow:
                from domain.request.value_objects import RequestId

                request_id = RequestId(value=query.request_id)
                request = uow.requests.get_by_id(request_id)
                if not request:
                    raise EntityNotFoundError("Request", query.request_id)

            # Get machines from storage
            machines = await self._get_machines_from_storage(query.request_id)
            self.logger.info(
                f"DEBUG: Found {len(machines)} machines in storage for request {query.request_id}"
            )

            # Update machine status if needed
            if not machines and request.resource_ids:
                self.logger.info(
                    f"DEBUG: No machines in storage but have resource IDs {request.resource_ids}, checking provider"
                )
                # No machines in storage but we have resource IDs - check provider and
                # create machines
                machines = await self._check_provider_and_create_machines(request)
                self.logger.info(f"DEBUG: Provider check returned {len(machines)} machines")
            elif machines:
                self.logger.info(f"DEBUG: Have {len(machines)} machines, updating status from AWS")
                # We have machines - update their status from AWS
                machines = await self._update_machine_status_from_aws(machines)
            else:
                self.logger.info(
                    f"DEBUG: No machines and no resource IDs for request {query.request_id}"
                )

            # Convert to DTO with machine data
            machines_data = []
            for machine in machines:
                machines_data.append(
                    {
                        "instance_id": str(machine.instance_id.value),
                        "status": machine.status.value,
                        "private_ip": machine.private_ip,
                        "public_ip": machine.public_ip,
                        "launch_time": machine.launch_time,
                        "launch_time_timestamp": (
                            machine.launch_time.timestamp() if machine.launch_time else 0
                        ),
                    }
                )

            # Create machine references from machine data
            from application.request.dto import MachineReferenceDTO

            machine_references = []
            for machine_data in machines_data:
                machine_ref = MachineReferenceDTO(
                    machine_id=machine_data["instance_id"],
                    name=machine_data.get("private_ip", machine_data["instance_id"]),
                    result=self._map_machine_status_to_result(machine_data["status"]),
                    status=machine_data["status"],
                    private_ip_address=machine_data.get("private_ip", ""),
                    public_ip_address=machine_data.get("public_ip"),
                    launch_time=int(machine_data.get("launch_time_timestamp", 0)),
                )
                machine_references.append(machine_ref)

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
                f"Retrieved request with {len(machines_data)} machines: {query.request_id}"
            )
            return request_dto

        except EntityNotFoundError:
            self.logger.error(f"Request not found: {query.request_id}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to get request: {e}")
            raise

    async def _get_machines_from_storage(self, request_id: str) -> List:
        """Get machines from storage for the request."""
        try:
            with self.uow_factory.create_unit_of_work() as uow:
                machines = uow.machines.find_by_request_id(request_id)
                return machines
        except Exception as e:
            self.logger.warning(
                f"Failed to get machines from storage for request {request_id}: {e}"
            )
            return []

    async def _check_provider_and_create_machines(self, request) -> List:
        """Check provider status and create machine aggregates using provider strategy pattern."""
        try:
            # Get provider context from container
            provider_context = self._get_provider_context()
            if not provider_context:
                self.logger.error("Provider context not available")
                return []

            # Create operation for resource-to-instance discovery using stored
            # provider API
            from providers.base.strategy import (
                ProviderOperation,
                ProviderOperationType,
            )

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
                f"Using provider strategy: {strategy_identifier} for request {request.request_id}"
            )
            self.logger.info(f"Operation parameters: {operation.parameters}")

            result = await provider_context.execute_with_strategy(strategy_identifier, operation)

            self.logger.info(
                f"Provider strategy result: success={result.success}, data_keys={list(result.data.keys()) if result.data else 'None'}"
            )

            if not result.success:
                self.logger.warning(
                    f"Failed to discover instances from resources: {result.error_message}"
                )
                return []

            # Get instance details from result
            instance_details = result.data.get("instances", [])
            if not instance_details:
                self.logger.info(f"No instances found for request {request.request_id}")
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
                    f"Created and saved {len(machines)} machines for request {request.request_id}"
                )

            return machines

        except Exception as e:
            self.logger.error(f"Failed to check provider and create machines: {e}")
            return []

    async def _update_machine_status_from_aws(self, machines: List) -> List:
        """Update machine status from AWS using existing handler methods."""
        try:
            # Group machines by request to use existing check_hosts_status methods
            if not machines:
                return []

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
            from providers.base.strategy import (
                ProviderOperation,
                ProviderOperationType,
            )

            # Extract instance IDs from machines
            instance_ids = [str(machine.instance_id.value) for machine in machines]

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
            result = await provider_context.execute_with_strategy(strategy_identifier, operation)

            if not result.success:
                self.logger.warning(f"Failed to check resource status: {result.error_message}")
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
            self.logger.warning(f"Failed to update machine status from AWS: {e}")
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

            def __init__(self, container):
                self.container = container

            async def check_resource_status(self, request) -> List[Dict[str, Any]]:
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

    def _create_machine_from_aws_data(self, aws_instance: Dict[str, Any], request):
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
            from infrastructure.caching.request_cache_service import (
                RequestCacheService,
            )

            config_manager = self._container.get(ConfigurationPort)
            cache_service = RequestCacheService(
                uow_factory=self.uow_factory,
                config_manager=config_manager,
                logger=self.logger,
            )
            return cache_service
        except Exception as e:
            self.logger.warning(f"Failed to initialize cache service: {e}")
            return None

    def _get_event_publisher(self):
        """Get event publisher for domain events."""
        try:
            from domain.base.ports import EventPublisherPort

            return self._container.get(EventPublisherPort)
        except Exception as e:
            self.logger.warning(f"Failed to initialize event publisher: {e}")

            # Return a no-op event publisher
            class NoOpEventPublisher:
                """No-operation event publisher that discards events."""

                def publish(self, event):
                    """Publish event (no-op implementation)."""

            return NoOpEventPublisher()


@query_handler(GetRequestStatusQuery)
class GetRequestStatusQueryHandler(BaseQueryHandler[GetRequestStatusQuery, str]):
    """Handler for getting request status."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: GetRequestStatusQuery) -> str:
        """Execute get request status query."""
        self.logger.info(f"Getting status for request: {query.request_id}")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                # Convert string to RequestId value object
                from domain.request.value_objects import RequestId

                request_id = RequestId(value=query.request_id)
                request = uow.requests.get_by_id(request_id)
                if not request:
                    raise EntityNotFoundError("Request", query.request_id)

                status = request.status.value
                self.logger.info(f"Request {query.request_id} status: {status}")
                return status

        except EntityNotFoundError:
            self.logger.error(f"Request not found: {query.request_id}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to get request status: {e}")
            raise


@query_handler(ListActiveRequestsQuery)
class ListActiveRequestsHandler(BaseQueryHandler[ListActiveRequestsQuery, List[RequestDTO]]):
    """Handler for listing active requests."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: ListActiveRequestsQuery) -> List[RequestDTO]:
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

                self.logger.info(f"Found {len(request_dtos)} active requests")
                return request_dtos

        except Exception as e:
            self.logger.error(f"Failed to list active requests: {e}")
            raise


@query_handler(ListReturnRequestsQuery)
class ListReturnRequestsHandler(BaseQueryHandler[ListReturnRequestsQuery, List[RequestDTO]]):
    """Handler for listing return requests."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: ListReturnRequestsQuery) -> List[RequestDTO]:
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

                self.logger.info(f"Found {len(request_dtos)} return requests")
                return request_dtos

        except Exception as e:
            self.logger.error(f"Failed to list return requests: {e}")
            raise


@query_handler(GetTemplateQuery)
class GetTemplateHandler(BaseQueryHandler[GetTemplateQuery, Template]):
    """Handler for getting template details."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
    ):
        super().__init__(logger, error_handler)
        self._container = container

    async def execute_query(self, query: GetTemplateQuery) -> Template:
        """Execute get template query."""
        from domain.template.aggregate import Template
        from infrastructure.template.configuration_manager import (
            TemplateConfigurationManager,
        )

        self.logger.info(f"Getting template: {query.template_id}")

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

            self.logger.info(f"Retrieved template: {query.template_id}")
            return domain_template

        except EntityNotFoundError:
            self.logger.error(f"Template not found: {query.template_id}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to get template: {e}")
            raise


@query_handler(ListTemplatesQuery)
class ListTemplatesHandler(BaseQueryHandler[ListTemplatesQuery, List[Template]]):
    """Handler for listing templates."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
    ):
        super().__init__(logger, error_handler)
        self._container = container

    async def execute_query(self, query: ListTemplatesQuery) -> List[Template]:
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
                        self.logger.warning(f"Skipping invalid template {dto.template_id}: {e}")
                        continue

            self.logger.info(f"Found {len(domain_templates)} templates")
            return domain_templates

        except Exception as e:
            self.logger.error(f"Failed to list templates: {e}")
            raise


@query_handler(ValidateTemplateQuery)
class ValidateTemplateHandler(BaseQueryHandler[ValidateTemplateQuery, ValidationDTO]):
    """Handler for validating template configuration."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: ValidateTemplateQuery) -> Dict[str, Any]:
        """Execute validate template query."""
        self.logger.info(f"Validating template: {query.template_id}")

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
                    f"Template validation failed for {query.template_id}: {validation_errors}"
                )
            else:
                self.logger.info(f"Template validation passed for {query.template_id}")

            return {
                "template_id": query.template_id,
                "is_valid": len(validation_errors) == 0,
                "validation_errors": validation_errors,
                "configuration": query.configuration,
            }

        except Exception as e:
            self.logger.error(f"Template validation failed for {query.template_id}: {e}")
            return {
                "template_id": query.template_id,
                "is_valid": False,
                "validation_errors": [f"Validation error: {str(e)}"],
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
    ):
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: GetMachineQuery) -> MachineDTO:
        """Execute get machine query."""
        self.logger.info(f"Getting machine: {query.machine_id}")

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

                self.logger.info(f"Retrieved machine: {query.machine_id}")
                return machine_dto

        except EntityNotFoundError:
            self.logger.error(f"Machine not found: {query.machine_id}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to get machine: {e}")
            raise


@query_handler(ListMachinesQuery)
class ListMachinesHandler(BaseQueryHandler[ListMachinesQuery, List[MachineDTO]]):
    """Handler for listing machines."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ):
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: ListMachinesQuery) -> List[MachineDTO]:
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

                self.logger.info(f"Found {len(machine_dtos)} machines")
                return machine_dtos

        except Exception as e:
            self.logger.error(f"Failed to list machines: {e}")
            raise
