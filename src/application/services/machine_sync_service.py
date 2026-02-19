"""Machine sync service for provider integration."""

from typing import Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    pass

from domain.base.ports.logging_port import LoggingPort
from domain.base.ports.container_port import ContainerPort
from domain.request.aggregate import Request
from domain.machine.aggregate import Machine
from infrastructure.di.buses import CommandBus


class MachineSyncService:
    """Provider synchronization service."""

    def __init__(
        self,
        command_bus: CommandBus,
        container: ContainerPort,
        logger: LoggingPort,
    ) -> None:
        self.command_bus = command_bus
        self.container = container
        self.logger = logger
        self._provider_registry_service = None  # Lazy loaded

    @property
    def provider_registry_service(self):
        """Lazy load provider registry service to avoid circular dependency."""
        if self._provider_registry_service is None:
            from application.services.provider_registry_service import ProviderRegistryService

            self._provider_registry_service = self.container.get(ProviderRegistryService)
        return self._provider_registry_service

    async def populate_missing_machine_ids(self, request: Request) -> None:
        """Populate missing machine IDs via command."""
        if request.needs_machine_id_population():
            try:
                from application.dto.commands import PopulateMachineIdsCommand

                populate_command = PopulateMachineIdsCommand(
                    request_id=str(request.request_id.value)
                )
                await self.command_bus.execute(populate_command)
                self.logger.debug(
                    f"Triggered machine ID population for request {request.request_id.value}"
                )
            except Exception as e:
                self.logger.error(f"Failed to populate machine IDs: {e}")

    async def fetch_provider_machines(
        self, request: Request, db_machines: list[Machine]
    ) -> Tuple[list[Machine], dict]:
        """Fetch machines from provider."""
        try:
            from providers.base.strategy import ProviderOperation, ProviderOperationType
            from domain.base.ports.configuration_port import ConfigurationPort

            # Use resource-level discovery when available (handles scaling/replacement)
            if request.resource_ids:
                operation_type = ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES
                parameters = {
                    "resource_ids": request.resource_ids,
                    "provider_api": request.provider_api or "RunInstances",
                    "template_id": request.template_id,
                }
            # Fallback to instance-level discovery for requests without resource tracking
            elif db_machines:
                operation_type = ProviderOperationType.GET_INSTANCE_STATUS
                instance_ids = [m.machine_id.value for m in db_machines]
                parameters = {
                    "instance_ids": instance_ids,
                    "template_id": request.template_id,
                }
            # Use machine_ids for return requests when available
            elif request.request_type.value == "return" and request.machine_ids:
                operation_type = ProviderOperationType.GET_INSTANCE_STATUS
                parameters = {
                    "instance_ids": request.machine_ids,
                    "template_id": request.template_id,
                }
            else:
                return [], {}

            operation = ProviderOperation(
                operation_type=operation_type,
                parameters=parameters,
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
                },
            )

            # Get provider configuration
            config_port = self.container.get(ConfigurationPort)
            config_port.get_provider_instance_config(request.provider_name)

            # Execute operation using Provider Registry Service
            result = await self.provider_registry_service.execute_operation(
                request.provider_name, operation
            )

            if result.success and result.data:
                instances = result.data.get("instances", [])
                self.logger.debug(f"Provider returned {len(instances)} instances")

                # Get machine adapter from container (proper DI)
                from providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter

                machine_adapter = self.container.get(AWSMachineAdapter)

                # Convert raw AWS instances to domain machines using machine adapter
                domain_machines = []
                for aws_instance_data in instances:
                    try:
                        processed_data = machine_adapter.create_machine_from_aws_instance(
                            aws_instance_data,
                            str(request.request_id),
                            request.provider_api or "RunInstances",
                            request.resource_ids[0] if request.resource_ids else "",
                        )

                        machine = self._create_machine_from_processed_data(processed_data, request)
                        domain_machines.append(machine)
                    except Exception as e:
                        self.logger.warning(f"Failed to create machine from AWS data: {e}")

                return domain_machines, result.metadata or {}
            else:
                self.logger.warning(f"Provider operation failed: {result.error_message}")
                return db_machines, {}

        except Exception as e:
            self.logger.error(f"Failed to fetch provider machines: {e}")
            return db_machines, {}

    def _create_machine_from_processed_data(
        self, processed_data: dict, request: Request
    ) -> Machine:
        from datetime import datetime
        from domain.base.value_objects import InstanceType
        from domain.machine.machine_identifiers import MachineId
        from domain.machine.machine_status import MachineStatus

        launch_time = processed_data.get("launch_time")
        if isinstance(launch_time, str):
            try:
                launch_time = datetime.fromisoformat(launch_time.replace("Z", "+00:00"))
            except ValueError:
                launch_time = None

        return Machine(
            machine_id=MachineId(value=processed_data["instance_id"]),
            name=processed_data.get("name"),
            template_id=request.template_id,
            request_id=str(request.request_id),
            provider_type=request.provider_type,
            provider_name=request.provider_name,
            provider_api=request.provider_api,
            resource_id=processed_data.get("resource_id"),
            instance_type=InstanceType(value=processed_data.get("instance_type", "t2.micro")),
            image_id=processed_data.get("image_id", "unknown"),
            price_type=processed_data.get("price_type"),
            status=MachineStatus(processed_data.get("status", "pending")),
            private_ip=processed_data.get("private_ip"),
            public_ip=processed_data.get("public_ip"),
            private_dns_name=processed_data.get("private_dns_name"),
            public_dns_name=processed_data.get("public_dns_name"),
            launch_time=launch_time,
            metadata=processed_data.get("metadata", {}),
        )

    async def sync_machines_with_provider(
        self, request: Request, db_machines: list[Machine], provider_machines: list[Machine]
    ) -> Tuple[list[Machine], dict]:
        try:
            from domain.base import UnitOfWorkFactory

            existing_by_id = {str(m.machine_id.value): m for m in db_machines}
            updated_machines = []
            to_upsert = []

            # Update existing machines and add new ones discovered from provider
            for provider_machine in provider_machines:
                machine_id = str(provider_machine.machine_id.value)
                existing = existing_by_id.get(machine_id)

                if existing:
                    # Check if machine needs update (including DNS, name, and price_type fields)
                    needs_update = (
                        existing.status != provider_machine.status
                        or existing.private_ip != provider_machine.private_ip
                        or existing.public_ip != provider_machine.public_ip
                        or existing.name != provider_machine.name
                        or existing.private_dns_name != provider_machine.private_dns_name
                        or existing.public_dns_name != provider_machine.public_dns_name
                        or existing.price_type != provider_machine.price_type
                    )

                    # Debug logging
                    self.logger.info(
                        f"Machine {machine_id} sync check: existing.name='{existing.name}' vs provider.name='{provider_machine.name}', needs_update={needs_update}"
                    )

                    if needs_update:
                        # Create updated machine with all provider data
                        machine_data = existing.model_dump()
                        machine_data["status"] = provider_machine.status
                        machine_data["private_ip"] = provider_machine.private_ip
                        machine_data["public_ip"] = provider_machine.public_ip
                        machine_data["name"] = provider_machine.name
                        machine_data["private_dns_name"] = provider_machine.private_dns_name
                        machine_data["public_dns_name"] = provider_machine.public_dns_name
                        machine_data["price_type"] = provider_machine.price_type
                        machine_data["public_ip"] = provider_machine.public_ip
                        machine_data["launch_time"] = (
                            provider_machine.launch_time or existing.launch_time
                        )
                        machine_data["version"] = existing.version + 1

                        updated_machine = Machine.model_validate(machine_data)
                        to_upsert.append(updated_machine)
                        updated_machines.append(updated_machine)

                        self.logger.debug(
                            f"Updated machine {machine_id} status: {existing.status} -> {provider_machine.status}"
                        )
                    else:
                        updated_machines.append(existing)
                else:
                    # New machine discovered from provider
                    to_upsert.append(provider_machine)
                    updated_machines.append(provider_machine)
                    self.logger.debug(f"Added new machine {machine_id} from provider")

            # Persist changes
            if to_upsert:
                uow_factory = self.container.get(UnitOfWorkFactory)
                with uow_factory.create_unit_of_work() as uow:
                    for machine in to_upsert:
                        uow.machines.save(machine)

                self.logger.info(f"Updated {len(to_upsert)} machines from provider sync")

            return updated_machines, {}

        except Exception as e:
            self.logger.error(f"Failed to sync machines with provider: {e}")
            return db_machines, {}
