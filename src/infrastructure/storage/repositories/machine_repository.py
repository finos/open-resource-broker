"""Single machine repository implementation using storage strategy composition."""

from datetime import datetime
from typing import Any, Optional

from domain.base.ports.storage_port import StoragePort
from domain.machine.aggregate import Machine
from domain.machine.machine_identifiers import MachineId
from domain.machine.repository import MachineRepository as MachineRepositoryInterface
from domain.machine.value_objects import MachineStatus
from infrastructure.error.decorators import handle_infrastructure_exceptions
from infrastructure.logging.logger import get_logger


class MachineSerializer:
    """Handles Machine aggregate serialization/deserialization."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.logger = get_logger(__name__)

    def to_dict(self, machine: Machine) -> dict[str, Any]:
        """Convert Machine aggregate to dictionary with additional fields."""
        try:
            return {
                # Core machine identification
                "machine_id": str(machine.machine_id.value),
                "template_id": machine.template_id,
                "request_id": machine.request_id,
                "return_request_id": machine.return_request_id,
                "provider_type": machine.provider_type,
                "provider_name": machine.provider_name,
                "provider_api": machine.provider_api,
                "resource_id": machine.resource_id,
                # Machine configuration
                "instance_type": str(machine.instance_type.value),
                "image_id": machine.image_id,
                # Network configuration
                "private_ip": machine.private_ip,
                "public_ip": machine.public_ip,
                "subnet_id": machine.subnet_id,
                "security_group_ids": machine.security_group_ids,
                # Machine state
                "status": machine.status.value,
                "status_reason": machine.status_reason,
                # Lifecycle timestamps
                "launch_time": (machine.launch_time.isoformat() if machine.launch_time else None),
                "termination_time": (
                    machine.termination_time.isoformat() if machine.termination_time else None
                ),
                # Tags and metadata
                "tags": machine.tags.to_dict() if machine.tags else {},
                "metadata": machine.metadata or {},
                # Provider-specific data
                "provider_data": machine.provider_data or {},
                # Versioning
                "version": machine.version,
                # Base entity fields
                "created_at": (machine.created_at.isoformat() if machine.created_at else None),
                "updated_at": (machine.updated_at.isoformat() if machine.updated_at else None),
                # Schema version for migration support
                "schema_version": "2.0.0",
            }
        except Exception as e:
            self.logger.error("Failed to serialize machine %s: %s", machine.machine_id, e)
            raise

    def from_dict(self, data: dict[str, Any]) -> Machine:
        """Convert dictionary to Machine aggregate with field support."""
        try:
            from domain.base.value_objects import InstanceType, Tags
            from domain.machine.machine_status import MachineStatus

            # Parse datetime fields
            launch_time = (
                datetime.fromisoformat(data["launch_time"]) if data.get("launch_time") else None
            )
            termination_time = (
                datetime.fromisoformat(data["termination_time"])
                if data.get("termination_time")
                else None
            )
            created_at = (
                datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
            )
            updated_at = (
                datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
            )

            # Build machine data with additional fields
            machine_data = {
                "machine_id": MachineId(value=data["machine_id"]),
                "template_id": data["template_id"],
                "request_id": data.get("request_id"),
                "return_request_id": data.get("return_request_id"),
                "provider_type": data.get("provider_type", "aws"),
                "provider_name": data.get("provider_name"),
                "provider_api": data.get("provider_api"),
                "resource_id": data.get("resource_id"),
                # Machine configuration
                "instance_type": InstanceType(value=data["instance_type"]),
                "image_id": data["image_id"],
                # Network configuration
                "private_ip": data.get("private_ip"),
                "public_ip": data.get("public_ip"),
                "subnet_id": data.get("subnet_id"),
                "security_group_ids": data.get("security_group_ids", []),
                # Machine state
                "status": MachineStatus(data.get("status", MachineStatus.PENDING.value)),
                "status_reason": data.get("status_reason"),
                # Lifecycle timestamps
                "launch_time": launch_time,
                "termination_time": termination_time,
                # Tags and metadata
                "tags": Tags(tags=data.get("tags", {})),
                "metadata": data.get("metadata", {}),
                # Provider-specific data
                "provider_data": data.get("provider_data", {}),
                # Versioning
                "version": data.get("version", 0),
                # Base entity fields
                "created_at": created_at,
                "updated_at": updated_at,
            }

            # Create machine using model_validate to handle all fields correctly
            machine = Machine.model_validate(machine_data)

            return machine

        except Exception as e:
            self.logger.error("Failed to deserialize machine data: %s", e)
            raise


class MachineRepositoryImpl(MachineRepositoryInterface):
    """Single machine repository implementation using storage strategy composition."""

    def __init__(self, storage_port: StoragePort) -> None:
        """Initialize repository with storage port."""
        if hasattr(storage_port, "entity_type"):
            storage_port.entity_type = "machines"

        self.storage_port = storage_port
        self.serializer = MachineSerializer()
        self.logger = get_logger(__name__)

    @handle_infrastructure_exceptions(context="machine_repository_save")
    def save(self, machine: Machine) -> list[Any]:
        """Save machine using storage strategy and return extracted events."""
        try:
            # Save the machine using machine_id as the key
            machine_data = self.serializer.to_dict(machine)
            self.storage_port.save(str(machine.machine_id.value), machine_data)

            # Extract events from the aggregate
            events = machine.get_domain_events()
            machine.clear_domain_events()

            self.logger.debug(
                "Saved machine %s and extracted %s events",
                machine.machine_id,
                len(events),
            )
            return events

        except Exception as e:
            self.logger.error("Failed to save machine %s: %s", machine.machine_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_save_batch")
    def save_batch(self, machines: list[Machine]) -> list[Any]:
        """Save multiple machines in a single storage operation when supported."""
        try:
            if not machines:
                return []

            entity_batch: dict[str, dict[str, Any]] = {}
            events: list[Any] = []

            for machine in machines:
                entity_id = str(machine.machine_id.value)
                entity_batch[entity_id] = self.serializer.to_dict(machine)
                events.extend(machine.get_domain_events())

            if hasattr(self.storage_port, "save_batch"):
                self.storage_port.save_batch(entity_batch)
            else:
                # Fallback for storage ports without batch support.
                for entity_id, machine_data in entity_batch.items():
                    self.storage_port.save(entity_id, machine_data)

            # Clear domain events only after a successful storage call.
            for machine in machines:
                machine.clear_domain_events()

            self.logger.debug(
                "Saved batch of %s machines and extracted %s events",
                len(entity_batch),
                len(events),
            )
            return events

        except Exception as e:
            self.logger.error("Failed to save batch of %s machines: %s", len(machines), e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_get_by_id")
    def get_by_id(self, machine_id: MachineId | str) -> Optional[Machine]:
        """Get machine by ID using storage strategy."""
        try:
            # Handle both MachineId objects and strings
            if isinstance(machine_id, MachineId):
                id_str = str(machine_id.value)
            else:
                id_str = str(machine_id)

            data = self.storage_port.find_by_id(id_str)
            if data:
                return self.serializer.from_dict(data)
            return None
        except Exception as e:
            self.logger.error("Failed to get machine %s: %s", machine_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_id")
    def find_by_id(self, machine_id: MachineId) -> Optional[Machine]:
        """Find machine by ID (alias for get_by_id)."""
        return self.get_by_id(machine_id)

    @handle_infrastructure_exceptions(context="machine_repository_find_by_instance_id")
    def find_by_instance_id(self, instance_id: MachineId) -> Optional[Machine]:
        """Find machine by instance ID (backward compatibility)."""
        try:
            criteria = {"machine_id": str(instance_id.value)}
            data_list = self.storage_port.find_by_criteria(criteria)
            if data_list:
                return self.serializer.from_dict(data_list[0])
            return None
        except Exception as e:
            self.logger.error("Failed to find machine by instance_id %s: %s", instance_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_machine_id")
    def find_by_machine_id(self, machine_id: MachineId) -> Optional[Machine]:
        """Find machine by machine ID."""
        try:
            criteria = {"machine_id": str(machine_id.value)}
            data_list = self.storage_port.find_by_criteria(criteria)
            if data_list:
                return self.serializer.from_dict(data_list[0])
            return None
        except Exception as e:
            self.logger.error("Failed to find machine by machine_id %s: %s", machine_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_template_id")
    def find_by_template_id(self, template_id: str) -> list[Machine]:
        """Find machines by template ID."""
        try:
            criteria = {"template_id": template_id}
            data_list = self.storage_port.find_by_criteria(criteria)
            return [self.serializer.from_dict(data) for data in data_list]
        except Exception as e:
            self.logger.error("Failed to find machines by template_id %s: %s", template_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_status")
    def find_by_status(self, status: MachineStatus) -> list[Machine]:
        """Find machines by status."""
        try:
            criteria = {"status": status.value}
            data_list = self.storage_port.find_by_criteria(criteria)
            return [self.serializer.from_dict(data) for data in data_list]
        except Exception as e:
            self.logger.error("Failed to find machines by status %s: %s", status, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_request_id")
    def find_by_request_id(self, request_id: str) -> list[Machine]:
        """Find machines by request ID."""
        try:
            criteria = {"request_id": request_id}
            data_list = self.storage_port.find_by_criteria(criteria)

            # Filter to only machine records (must have machine_id field)
            machine_data_list = [data for data in data_list if "machine_id" in data]

            return [self.serializer.from_dict(data) for data in machine_data_list]
        except Exception as e:
            self.logger.error("Failed to find machines by request_id %s: %s", request_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_return_request_id")
    def find_by_return_request_id(self, return_request_id: str) -> list[Machine]:
        """Find machines by return request ID."""
        try:
            criteria = {"return_request_id": return_request_id}
            data_list = self.storage_port.find_by_criteria(criteria)
            machine_data_list = [data for data in data_list if "machine_id" in data]
            return [self.serializer.from_dict(data) for data in machine_data_list]
        except Exception as e:
            self.logger.error("Failed to find machines by return_request_id %s: %s", return_request_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_active_machines")
    def find_active_machines(self) -> list[Machine]:
        """Find all active (non-terminated) machines."""
        try:
            from domain.machine.value_objects import MachineStatus

            active_statuses = [
                MachineStatus.PENDING,
                MachineStatus.RUNNING,
                MachineStatus.LAUNCHING,
            ]
            all_machines = []

            for status in active_statuses:
                machines = self.find_by_status(status)
                all_machines.extend(machines)

            return all_machines
        except Exception as e:
            self.logger.error("Failed to find active machines: %s", e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_all")
    def find_all(self) -> list[Machine]:
        """Find all machines."""
        try:
            all_data = self.storage_port.find_all()
            return [self.serializer.from_dict(data) for data in all_data.values()]
        except Exception as e:
            self.logger.error("Failed to find all machines: %s", e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_delete")
    def delete(self, machine_id: MachineId) -> None:
        """Delete machine by ID."""
        try:
            self.storage_port.delete(str(machine_id.value))
            self.logger.debug("Deleted machine %s", machine_id)
        except Exception as e:
            self.logger.error("Failed to delete machine %s: %s", machine_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_exists")
    def exists(self, machine_id: MachineId) -> bool:
        """Check if machine exists."""
        try:
            return self.storage_port.exists(str(machine_id.value))
        except Exception as e:
            self.logger.error("Failed to check if machine %s exists: %s", machine_id, e)
            raise
