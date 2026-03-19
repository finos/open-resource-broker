"""Machine sync service for provider integration."""

from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from orb.application.services.provider_registry_service import ProviderRegistryService

from orb.application.ports.command_bus_port import CommandBusPort
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request


class MachineSyncService:
    """Provider synchronization service."""

    def __init__(
        self,
        command_bus: CommandBusPort,
        uow_factory: UnitOfWorkFactory,
        config_port: ConfigurationPort,
        logger: LoggingPort,
        provider_registry_service: Optional["ProviderRegistryService"] = None,
    ) -> None:
        self.command_bus = command_bus
        self.uow_factory = uow_factory
        self._config_port = config_port
        self.logger = logger
        self._provider_registry_service: Optional[ProviderRegistryService] = (
            provider_registry_service
        )

    @property
    def provider_registry_service(self) -> Optional["ProviderRegistryService"]:
        """Return the injected provider registry service."""
        return self._provider_registry_service

    async def populate_missing_machine_ids(self, request: Request) -> None:
        """Populate missing machine IDs via command."""
        if request.needs_machine_id_population():
            try:
                from orb.application.dto.commands import PopulateMachineIdsCommand

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
            from orb.domain.base.operations import (
                Operation as ProviderOperation,
                OperationType as ProviderOperationType,
            )

            # For return requests, always use instance-level status for the specific
            # machines being returned — not resource-level discovery which returns all
            # ASG/fleet instances including unrelated ones.
            if request.request_type.value == "return" and request.machine_ids:
                operation_type = ProviderOperationType.GET_INSTANCE_STATUS
                parameters = {
                    "instance_ids": request.machine_ids,
                    "template_id": request.template_id,
                }
            # Use resource-level discovery for acquire requests (handles scaling/replacement)
            elif request.resource_ids:
                operation_type = ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES
                parameters = {
                    "resource_ids": request.resource_ids,
                    "provider_api": request.provider_api,
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
            self._config_port.get_provider_instance_config(request.provider_name or "")

            # Execute operation using Provider Registry Service
            if self.provider_registry_service is None:
                raise RuntimeError("ProviderRegistryService is required for this operation")
            result = await self.provider_registry_service.execute_operation(
                request.provider_name or "", operation
            )

            if result.success and result.data:
                instances = result.data.get("instances", [])
                self.logger.debug(f"Provider returned {len(instances)} instances")

                # instances are already snake_case domain dicts from check_hosts_status
                # via _get_instance_details → machine_adapter (PascalCase→snake_case conversion
                # happens once in the infrastructure layer). No re-conversion needed here.
                domain_machines = []
                returned_ids = set()
                db_machines_by_id = {str(m.machine_id.value): m for m in db_machines}
                for instance_data in instances:
                    try:
                        processed_data = {
                            **instance_data,
                            "request_id": str(request.request_id),
                            "resource_id": request.resource_ids[0] if request.resource_ids else "",
                        }
                        terminal_states = {"shutting-down", "terminated", "stopping", "stopped"}
                        instance_status = processed_data.get("status", "")
                        existing = db_machines_by_id.get(processed_data.get("instance_id", ""))
                        if instance_status in terminal_states and existing:
                            machine = self._create_machine_with_status(
                                existing, MachineStatus(instance_status)
                            )
                        else:
                            machine = self._create_machine_from_processed_data(
                                processed_data, request
                            )
                        domain_machines.append(machine)
                        returned_ids.add(processed_data["instance_id"])
                    except Exception as e:
                        self.logger.warning(f"Failed to create machine from instance data: {e}")

                # For return requests: instances missing from AWS response have been
                # terminated and purged (~1hr window). Treat them as terminated.
                if request.request_type.value == "return":
                    queried_ids = set(parameters.get("instance_ids", []))
                    missing_ids = queried_ids - returned_ids
                    for missing_id in missing_ids:
                        self.logger.info(
                            f"Instance {missing_id} not found in AWS — treating as terminated"
                        )
                        existing = next(
                            (m for m in db_machines if m.machine_id.value == missing_id), None
                        )
                        if existing:
                            domain_machines.append(self._create_terminated_machine(existing))

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

        from orb.domain.base.value_objects import InstanceType
        from orb.domain.machine.machine_identifiers import MachineId
        from orb.domain.machine.machine_status import MachineStatus

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
            subnet_id=processed_data.get("subnet_id"),
            security_group_ids=processed_data.get("security_group_ids", []),
            metadata=processed_data.get("metadata", {}),
        )

    def _create_terminated_machine(self, existing: Machine) -> Machine:
        """Return a copy of an existing DB machine with status set to TERMINATED."""
        from orb.domain.machine.machine_status import MachineStatus

        machine_data = existing.model_dump()
        machine_data["status"] = MachineStatus.TERMINATED
        machine_data["version"] = existing.version + 1
        return Machine.model_validate(machine_data)

    def _create_machine_with_status(self, existing: Machine, status: "MachineStatus") -> Machine:
        """Return a copy of an existing DB machine with only the status updated."""
        machine_data = existing.model_dump()
        machine_data["status"] = status
        machine_data["version"] = existing.version + 1
        return Machine.model_validate(machine_data)

    async def sync_machines_with_provider(
        self, request: Request, db_machines: list[Machine], provider_machines: list[Machine]
    ) -> Tuple[list[Machine], dict]:
        try:
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
                        or existing.subnet_id != provider_machine.subnet_id
                        or existing.security_group_ids != provider_machine.security_group_ids
                        or existing.vpc_id != provider_machine.vpc_id
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
                        machine_data["subnet_id"] = provider_machine.subnet_id
                        machine_data["security_group_ids"] = provider_machine.security_group_ids
                        machine_data["vpc_id"] = provider_machine.vpc_id
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
                with self.uow_factory.create_unit_of_work() as uow:
                    for machine in to_upsert:
                        uow.machines.save(machine)

                self.logger.info(f"Updated {len(to_upsert)} machines from provider sync")

            return updated_machines, {}

        except Exception as e:
            self.logger.error(f"Failed to sync machines with provider: {e}")
            return db_machines, {}
