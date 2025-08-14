"""Single machine repository implementation using storage strategy composition."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from src.domain.base.ports.storage_port import StoragePort
from src.domain.base.value_objects import InstanceId
from src.domain.machine.aggregate import Machine
from src.domain.machine.repository import (
    MachineRepository as MachineRepositoryInterface,
)
from src.domain.machine.value_objects import MachineId, MachineStatus
from src.infrastructure.error.decorators import handle_infrastructure_exceptions
from src.infrastructure.logging.logger import get_logger


class MachineSerializer:
    """Handles Machine aggregate serialization/deserialization."""

    def __init__(self):
        """Initialize the instance."""
        self.logger = get_logger(__name__)

    def to_dict(self, machine: Machine) -> Dict[str, Any]:
        """Convert Machine aggregate to dictionary with additional fields."""
        try:
            return {
                # Core machine identification
                "instance_id": str(machine.instance_id.value),
                "template_id": machine.template_id,
                "request_id": machine.request_id,
                "provider_type": machine.provider_type,
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
            self.logger.error(f"Failed to serialize machine {machine.instance_id}: {e}")
            raise

    def from_dict(self, data: Dict[str, Any]) -> Machine:
        """Convert dictionary to Machine aggregate with field support."""
        try:
            from src.domain.base.value_objects import InstanceType, Tags
            from src.domain.machine.machine_status import MachineStatus

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
                # Core machine identification
                "instance_id": InstanceId(value=data["instance_id"]),
                "template_id": data["template_id"],
                "request_id": data.get("request_id"),
                "provider_type": data.get("provider_type", "aws"),
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
            self.logger.error(f"Failed to deserialize machine data: {e}")
            raise


class MachineRepositoryImpl(MachineRepositoryInterface):
    """Single machine repository implementation using storage strategy composition."""

    def __init__(self, storage_port: StoragePort):
        """Initialize repository with storage port."""
        self.storage_port = storage_port
        self.serializer = MachineSerializer()
        self.logger = get_logger(__name__)

    @handle_infrastructure_exceptions(context="machine_repository_save")
    def save(self, machine: Machine) -> List[Any]:
        """Save machine using storage strategy and return extracted events."""
        try:
            # Save the machine using instance_id as the key
            machine_data = self.serializer.to_dict(machine)
            self.storage_port.save(str(machine.instance_id.value), machine_data)

            # Extract events from the aggregate
            events = machine.get_domain_events()
            machine.clear_domain_events()

            self.logger.debug(
                f"Saved machine { machine.instance_id} and extracted { len(events)} events"
            )
            return events

        except Exception as e:
            self.logger.error(f"Failed to save machine {machine.instance_id}: {e}")
            raise

    @handle_infrastructure_exceptions(context="machine_repository_get_by_id")
    def get_by_id(self, machine_id: MachineId) -> Optional[Machine]:
        """Get machine by ID using storage strategy."""
        try:
            data = self.storage_port.find_by_id(str(machine_id.value))
            if data:
                return self.serializer.from_dict(data)
            return None
        except Exception as e:
            self.logger.error(f"Failed to get machine {machine_id}: {e}")
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_id")
    def find_by_id(self, machine_id: MachineId) -> Optional[Machine]:
        """Find machine by ID (alias for get_by_id)."""
        return self.get_by_id(machine_id)

    @handle_infrastructure_exceptions(context="machine_repository_find_by_instance_id")
    def find_by_instance_id(self, instance_id: InstanceId) -> Optional[Machine]:
        """Find machine by instance ID."""
        try:
            criteria = {"instance_id": str(instance_id.value)}
            data_list = self.storage_port.find_by_criteria(criteria)
            if data_list:
                return self.serializer.from_dict(data_list[0])
            return None
        except Exception as e:
            self.logger.error(f"Failed to find machine by instance_id {instance_id}: {e}")
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_template_id")
    def find_by_template_id(self, template_id: str) -> List[Machine]:
        """Find machines by template ID."""
        try:
            criteria = {"template_id": template_id}
            data_list = self.storage_port.find_by_criteria(criteria)
            return [self.serializer.from_dict(data) for data in data_list]
        except Exception as e:
            self.logger.error(f"Failed to find machines by template_id {template_id}: {e}")
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_status")
    def find_by_status(self, status: MachineStatus) -> List[Machine]:
        """Find machines by status."""
        try:
            criteria = {"status": status.value}
            data_list = self.storage_port.find_by_criteria(criteria)
            return [self.serializer.from_dict(data) for data in data_list]
        except Exception as e:
            self.logger.error(f"Failed to find machines by status {status}: {e}")
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_request_id")
    def find_by_request_id(self, request_id: str) -> List[Machine]:
        """Find machines by request ID."""
        try:
            criteria = {"request_id": request_id}
            data_list = self.storage_port.find_by_criteria(criteria)

            # Filter to only machine records (must have instance_id field)
            machine_data_list = [data for data in data_list if "instance_id" in data]

            return [self.serializer.from_dict(data) for data in machine_data_list]
        except Exception as e:
            self.logger.error(f"Failed to find machines by request_id {request_id}: {e}")
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_active_machines")
    def find_active_machines(self) -> List[Machine]:
        """Find all active (non-terminated) machines."""
        try:
            from src.domain.machine.value_objects import MachineStatus

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
            self.logger.error(f"Failed to find active machines: {e}")
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_all")
    def find_all(self) -> List[Machine]:
        """Find all machines."""
        try:
            all_data = self.storage_port.find_all()
            return [self.serializer.from_dict(data) for data in all_data.values()]
        except Exception as e:
            self.logger.error(f"Failed to find all machines: {e}")
            raise

    @handle_infrastructure_exceptions(context="machine_repository_delete")
    def delete(self, machine_id: MachineId) -> None:
        """Delete machine by ID."""
        try:
            self.storage_port.delete(str(machine_id.value))
            self.logger.debug(f"Deleted machine {machine_id}")
        except Exception as e:
            self.logger.error(f"Failed to delete machine {machine_id}: {e}")
            raise

    @handle_infrastructure_exceptions(context="machine_repository_exists")
    def exists(self, machine_id: MachineId) -> bool:
        """Check if machine exists."""
        try:
            return self.storage_port.exists(str(machine_id.value))
        except Exception as e:
            self.logger.error(f"Failed to check if machine {machine_id} exists: {e}")
            raise
