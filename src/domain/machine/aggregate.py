"""Machine aggregate - core machine domain logic."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from domain.base.entity import AggregateRoot
from domain.base.value_objects import InstanceId, InstanceType, IPAddress, Tags

from .machine_status import MachineStatus


class Machine(AggregateRoot):
    """Machine aggregate root with both snake_case and camelCase support via aliases."""

    model_config = ConfigDict(
        frozen=False,
        validate_assignment=True,
        populate_by_name=True,  # Allow both field names and aliases
    )

    # Core machine identification
    instance_id: InstanceId
    template_id: str
    request_id: Optional[str] = None  # Link to the request that created this machine
    provider_type: str

    # Machine configuration
    instance_type: InstanceType
    image_id: str

    # Network configuration
    private_ip: Optional[str] = None
    public_ip: Optional[str] = None
    subnet_id: Optional[str] = None
    security_group_ids: List[str] = Field(default_factory=list)

    # Machine state
    status: MachineStatus = Field(default=MachineStatus.PENDING)
    status_reason: Optional[str] = None

    # Lifecycle timestamps
    launch_time: Optional[datetime] = None
    termination_time: Optional[datetime] = None

    # Tags and metadata
    tags: Tags = Field(default_factory=lambda: Tags(tags={}))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Provider-specific data
    provider_data: Dict[str, Any] = Field(default_factory=dict)

    # Versioning
    version: int = Field(default=0)

    def __init__(self, **data) -> None:
        """Initialize the instance."""
        # Set default ID if not provided
        if "id" not in data:
            data["id"] = data.get("instance_id", f"machine-{data.get('template_id', 'unknown')}")

        # Set default timestamps if not provided
        from datetime import datetime

        if "created_at" not in data:
            data["created_at"] = datetime.utcnow()

        super().__init__(**data)

    def update_status(self, new_status: MachineStatus, reason: Optional[str] = None) -> "Machine":
        """Update machine status and generate domain event."""
        old_status = self.status

        data = self.model_dump()
        data["status"] = new_status
        data["status_reason"] = reason
        data["version"] = self.version + 1

        # Update timestamps based on status
        now = datetime.utcnow()
        if new_status == MachineStatus.RUNNING and not self.launch_time:
            data["launch_time"] = now
        elif new_status in [MachineStatus.TERMINATED, MachineStatus.FAILED]:
            data["termination_time"] = now

        # Create updated machine instance
        updated_machine = Machine.model_validate(data)

        # Generate domain event for status change (only if status actually changed)
        if old_status != new_status:
            from domain.base.events.domain_events import MachineStatusChangedEvent

            status_event = MachineStatusChangedEvent(
                # DomainEvent required fields
                aggregate_id=str(self.instance_id),
                aggregate_type="Machine",
                # MachineEvent required fields
                machine_id=str(self.instance_id),
                request_id=str(self.request_id) if self.request_id else "unknown",
                # StatusChangeEvent required fields
                old_status=old_status.value,
                new_status=new_status.value,
                reason=reason,
                # Additional metadata in the metadata field
                metadata={
                    "reason": reason,
                    "timestamp": now.isoformat(),
                    "machine_type": str(self.instance_type),
                    "provider_type": self.provider_type,
                },
            )
            updated_machine.add_domain_event(status_event)

        return updated_machine

    def get_id(self) -> str:
        """Get the machine identifier."""
        return str(self.instance_id)

    def update_network_info(
        self, private_ip: Optional[str] = None, public_ip: Optional[str] = None
    ) -> "Machine":
        """Update machine network information."""
        data = self.model_dump()

        if private_ip:
            data["private_ip"] = IPAddress(value=private_ip)
        if public_ip:
            data["public_ip"] = IPAddress(value=public_ip)

        data["version"] = self.version + 1
        return Machine.model_validate(data)

    def update_tags(self, new_tags: Tags) -> "Machine":
        """Update machine tags."""
        merged_tags = self.tags.merge(new_tags)
        data = self.model_dump()
        data["tags"] = merged_tags
        data["version"] = self.version + 1
        return Machine.model_validate(data)

    def set_provider_data(self, provider_data: Dict[str, Any]) -> "Machine":
        """Set provider-specific data."""
        data = self.model_dump()
        data["provider_data"] = provider_data
        data["version"] = self.version + 1
        return Machine.model_validate(data)

    def get_provider_data(self, key: str, default: Any = None) -> Any:
        """Get provider-specific data value."""
        return self.provider_data.get(key, default)

    @property
    def is_running(self) -> bool:
        """Check if machine is running."""
        return self.status == MachineStatus.RUNNING

    @property
    def is_terminated(self) -> bool:
        """Check if machine is terminated."""
        return self.status in [MachineStatus.TERMINATED, MachineStatus.SHUTTING_DOWN]

    @property
    def is_healthy(self) -> bool:
        """Check if machine is in a healthy state."""
        return self.status in [MachineStatus.PENDING, MachineStatus.RUNNING]

    @property
    def uptime(self) -> Optional[int]:
        """Get machine uptime in seconds."""
        if self.launch_time and self.status == MachineStatus.RUNNING:
            return int((datetime.utcnow() - self.launch_time).total_seconds())
        return None

    def to_provider_format(self, provider_type: str) -> Dict[str, Any]:
        """Convert machine to provider-specific format."""
        base_format = {
            "instance_id": self.instance_id.value,
            "template_id": self.template_id,
            "provider_type": self.provider_type,
            "instance_type": self.instance_type.value,
            "image_id": self.image_id,
            "status": self.status.value,
            "status_reason": self.status_reason,
            "subnet_id": self.subnet_id,
            "security_group_ids": self.security_group_ids,
            "tags": self.tags.to_dict(),
            "metadata": self.metadata,
            "provider_data": self.provider_data,
            "version": self.version,
        }

        # Add optional fields
        if self.private_ip:
            base_format["private_ip"] = self.private_ip.value
        if self.public_ip:
            base_format["public_ip"] = self.public_ip.value
        if self.launch_time:
            base_format["launch_time"] = self.launch_time.isoformat()
        if self.termination_time:
            base_format["termination_time"] = self.termination_time.isoformat()

        return base_format

    @classmethod
    def from_provider_format(cls, data: Dict[str, Any], provider_type: str) -> "Machine":
        """Create machine from provider-specific format."""
        core_data = {
            "instance_id": InstanceId(value=data.get("instance_id")),
            "template_id": data.get("template_id"),
            "provider_type": provider_type,
            "instance_type": InstanceType(value=data.get("instance_type")),
            "image_id": data.get("image_id"),
            "status": MachineStatus(data.get("status", MachineStatus.UNKNOWN.value)),
            "status_reason": data.get("status_reason"),
            "subnet_id": data.get("subnet_id"),
            "security_group_ids": data.get("security_group_ids", []),
            "tags": Tags.from_dict(data.get("tags", {})),
            "metadata": data.get("metadata", {}),
            "provider_data": data.get("provider_data", {}),
            "version": data.get("version", 0),
        }

        # Handle optional fields
        if "private_ip" in data and data["private_ip"]:
            core_data["private_ip"] = IPAddress(value=data["private_ip"])
        if "public_ip" in data and data["public_ip"]:
            core_data["public_ip"] = IPAddress(value=data["public_ip"])
        if "launch_time" in data and data["launch_time"]:
            core_data["launch_time"] = datetime.fromisoformat(data["launch_time"])
        if "termination_time" in data and data["termination_time"]:
            core_data["termination_time"] = datetime.fromisoformat(data["termination_time"])

        return cls.model_validate(core_data)
