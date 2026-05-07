"""Machine aggregate - core machine domain logic."""

from datetime import datetime, timezone
from typing import Any, ClassVar, Optional

from pydantic import ConfigDict, Field

from orb.domain.base.entity import AggregateRoot
from orb.domain.base.value_objects import InstanceType, IPAddress, Tags
from orb.domain.machine.exceptions import InvalidMachineStateError
from orb.domain.machine.machine_identifiers import MachineId

from .machine_status import MachineStatus


class Machine(AggregateRoot):
    """Machine aggregate root with both snake_case and camelCase support via aliases."""

    model_config = ConfigDict(
        frozen=False,
        validate_assignment=True,
        populate_by_name=True,  # Allow both field names and aliases
    )

    # Fields that are intentionally NOT persisted by MachineSerializer.
    # Adding anything here is a deliberate decision to drop it on save.
    # If you add a field to Machine, either add it to MachineSerializer
    # or add it to this set with a comment explaining why.
    _SERIALIZATION_EXCLUDED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            # Inherited from Entity; used as an internal Pydantic/aggregate identity
            # key but machine_id is the canonical persisted identifier.
            "id",
        }
    )

    # Core machine identification
    machine_id: MachineId
    name: Optional[str] = None
    template_id: str
    request_id: Optional[str] = None
    return_request_id: Optional[str] = None
    provider_type: str = Field(default="aws")
    provider_name: str
    provider_api: Optional[str] = None
    resource_id: Optional[str] = None

    # Machine configuration
    instance_type: InstanceType
    image_id: str
    price_type: Optional[str] = None

    # Network configuration
    private_ip: Optional[str] = None
    public_ip: Optional[str] = None
    private_dns_name: Optional[str] = None
    public_dns_name: Optional[str] = None
    subnet_id: Optional[str] = None
    security_group_ids: list[str] = Field(default_factory=list)
    vpc_id: Optional[str] = None

    # Machine state
    status: MachineStatus = Field(default=MachineStatus.PENDING)
    status_reason: Optional[str] = None

    # Lifecycle timestamps
    launch_time: Optional[datetime] = None
    """AWS-reported timestamp when the instance actually started running.
    Set when the machine transitions to RUNNING status (sourced from the
    cloud provider, e.g. EC2 LaunchTime). Used for uptime calculations,
    DTOs, and external consumers."""
    provisioning_started_at: Optional[datetime] = None
    """ORB-internal timestamp recording when this broker initiated the
    launch sequence (i.e. when start_launching() was called and the
    machine moved from PENDING → LAUNCHING). Not propagated to DTOs or
    external consumers — use launch_time for provider-reported start time."""
    termination_time: Optional[datetime] = None

    # Tags and metadata
    tags: Tags = Field(default_factory=lambda: Tags(tags={}))
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Provider-specific data
    provider_data: dict[str, Any] = Field(default_factory=dict)

    # Versioning
    version: int = Field(default=0)

    def __init__(self, **data) -> None:
        """Initialize the instance."""
        # Handle instance_id parameter (map to machine_id)
        if "instance_id" in data and "machine_id" not in data:
            from orb.domain.base.value_objects import InstanceId

            instance_id = data.pop("instance_id")
            if isinstance(instance_id, InstanceId):
                data["machine_id"] = MachineId(value=instance_id.value)
            else:
                data["machine_id"] = MachineId(value=str(instance_id))

        # Set default ID if not provided
        if "id" not in data:
            data["id"] = data.get("machine_id", f"machine-{data.get('template_id', 'unknown')}")

        # Set default timestamps if not provided
        if "created_at" not in data:
            data["created_at"] = datetime.now(timezone.utc)

        super().__init__(**data)

    def start_launching(self) -> "Machine":
        """Transition machine from PENDING to LAUNCHING status."""
        if self.status != MachineStatus.PENDING:
            raise InvalidMachineStateError(self.status.value, MachineStatus.LAUNCHING.value)

        fields = self.model_dump()
        fields["status"] = MachineStatus.LAUNCHING
        fields["provisioning_started_at"] = datetime.now(timezone.utc)
        fields["version"] = self.version + 1

        updated_machine = Machine.model_validate(fields)

        # Generate domain event for status change
        from orb.domain.base.events.domain_events import MachineStatusChangedEvent

        status_event = MachineStatusChangedEvent(
            aggregate_id=str(self.machine_id),
            aggregate_type="Machine",
            machine_id=str(self.machine_id),
            old_status=self.status.value,
            new_status=MachineStatus.LAUNCHING.value,
            reason="Machine launching initiated",
            metadata={
                "reason": "Machine launching initiated",
                "timestamp": fields["provisioning_started_at"].isoformat(),
                "machine_type": str(self.instance_type),
                "provider_type": self.provider_type,
            },
        )
        updated_machine.add_domain_event(status_event)

        return updated_machine

    def update_status(self, new_status: MachineStatus, reason: Optional[str] = None) -> "Machine":
        """Update machine status and generate domain event."""
        old_status = self.status

        fields = self.model_dump()
        fields["status"] = new_status
        fields["status_reason"] = reason
        fields["version"] = self.version + 1

        # Update timestamps based on status
        now = datetime.now(timezone.utc)
        if new_status == MachineStatus.RUNNING and not self.launch_time:
            fields["launch_time"] = now
        elif new_status in [MachineStatus.TERMINATED, MachineStatus.FAILED]:
            fields["termination_time"] = now

        # Create updated machine instance
        updated_machine = Machine.model_validate(fields)

        # Generate domain event for status change (only if status actually changed)
        if old_status != new_status:
            from orb.domain.base.events.domain_events import MachineStatusChangedEvent

            status_event = MachineStatusChangedEvent(
                # DomainEvent required fields
                aggregate_id=str(self.machine_id),
                aggregate_type="Machine",
                # MachineEvent required fields
                machine_id=str(self.machine_id),
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

            # Fire MachineProvisionedEvent when machine becomes RUNNING with an IP
            if new_status == MachineStatus.RUNNING and (
                updated_machine.private_ip or updated_machine.public_ip
            ):
                from orb.domain.base.events.domain_events import MachineProvisionedEvent

                provisioned_event = MachineProvisionedEvent(
                    aggregate_id=str(self.machine_id),
                    aggregate_type="Machine",
                    machine_id=str(self.machine_id),
                    private_ip=str(updated_machine.private_ip)
                    if updated_machine.private_ip
                    else None,
                    public_ip=str(updated_machine.public_ip) if updated_machine.public_ip else None,
                    provisioning_time=now,
                )
                updated_machine.add_domain_event(provisioned_event)

        return updated_machine

    def get_id(self) -> str:
        """Get the machine identifier."""
        return str(self.machine_id)

    def update_network_info(
        self, private_ip: Optional[str] = None, public_ip: Optional[str] = None
    ) -> "Machine":
        """Update machine network information."""
        fields = self.model_dump()

        if private_ip:
            fields["private_ip"] = IPAddress(value=private_ip)
        if public_ip:
            fields["public_ip"] = IPAddress(value=public_ip)

        fields["version"] = self.version + 1
        return Machine.model_validate(fields)

    def update_tags(self, new_tags: Tags) -> "Machine":
        """Update machine tags."""
        merged_tags = self.tags.merge(new_tags)
        fields = self.model_dump()
        fields["tags"] = merged_tags
        fields["version"] = self.version + 1
        return Machine.model_validate(fields)

    def set_provider_data(self, provider_data: dict[str, Any]) -> "Machine":
        """Set provider-specific data."""
        fields = self.model_dump()
        fields["provider_data"] = provider_data
        fields["version"] = self.version + 1
        return Machine.model_validate(fields)

    def get_provider_data(self, key: str, default: Any = None) -> Any:
        """Get provider-specific data value."""
        return self.provider_data.get(key, default)

    @property
    def display_name(self) -> str:
        """Resolve a human-readable name for the machine.

        Resolution chain (first non-empty value wins):
          1. ``name``            — explicitly set name
          2. ``private_dns_name`` — AWS-assigned private DNS
          3. ``public_dns_name``  — AWS-assigned public DNS
          4. ``private_ip``       — private IP address
          5. ``str(machine_id)``  — always available fallback
        """
        return (
            self.name
            or self.private_dns_name
            or self.public_dns_name
            or (str(self.private_ip) if self.private_ip else None)
            or str(self.machine_id)
        )

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
            return int((datetime.now(timezone.utc) - self.launch_time).total_seconds())
        return None

    def to_provider_format(self, provider_type: str) -> dict[str, Any]:
        """Convert machine to provider-specific format."""
        base_format = {
            "instance_id": self.machine_id.value,
            "template_id": self.template_id,
            "provider_type": self.provider_type,
            "instance_type": self.instance_type.value,
            "image_id": self.image_id,
            "status": self.status.value,
            "status_reason": self.status_reason,
            "subnet_id": self.subnet_id,
            "security_group_ids": self.security_group_ids,
            "vpc_id": self.vpc_id,
            "tags": self.tags.to_dict(),
            "metadata": self.metadata,
            "provider_data": self.provider_data,
            "version": self.version,
        }

        # Add optional fields
        if self.private_ip:
            base_format["private_ip"] = str(self.private_ip)
        if self.public_ip:
            base_format["public_ip"] = str(self.public_ip)
        if self.launch_time:
            base_format["launch_time"] = self.launch_time.isoformat()
        if self.termination_time:
            base_format["termination_time"] = self.termination_time.isoformat()

        return base_format

    @classmethod
    def from_provider_format(cls, data: dict[str, Any], provider_type: str) -> "Machine":
        """Create machine from provider-specific format."""
        core_data = {
            "machine_id": MachineId(value=data.get("instance_id") or ""),
            "template_id": data.get("template_id"),
            "provider_type": provider_type,
            "instance_type": InstanceType(value=data.get("instance_type") or ""),
            "image_id": data.get("image_id"),
            "status": MachineStatus(data.get("status", MachineStatus.UNKNOWN.value)),
            "status_reason": data.get("status_reason"),
            "subnet_id": data.get("subnet_id"),
            "security_group_ids": data.get("security_group_ids", []),
            "vpc_id": data.get("vpc_id"),
            "tags": Tags.from_dict(data.get("tags", {})),
            "metadata": data.get("metadata", {}),
            "provider_data": data.get("provider_data", {}),
            "version": data.get("version", 0),
        }

        # Handle optional fields
        if data.get("private_ip"):
            core_data["private_ip"] = IPAddress(value=data["private_ip"])
        if data.get("public_ip"):
            core_data["public_ip"] = IPAddress(value=data["public_ip"])
        if data.get("launch_time"):
            core_data["launch_time"] = datetime.fromisoformat(data["launch_time"])
        if data.get("termination_time"):
            core_data["termination_time"] = datetime.fromisoformat(data["termination_time"])

        return cls.model_validate(core_data)
