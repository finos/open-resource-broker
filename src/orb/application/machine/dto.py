"""Data Transfer Objects for machine domain operations."""

from datetime import datetime
from typing import Any, Optional, Union

from pydantic import Field

from orb.application.dto.base import BaseDTO
from orb.application.machine.result_mapping import map_machine_status_to_result
from orb.domain.machine.aggregate import Machine


class MachineDTO(BaseDTO):
    """DTO for machine responses."""

    machine_id: str
    name: str
    status: str
    instance_type: str
    private_ip: str
    public_ip: Optional[str] = None
    result: str  # 'executing', 'fail', or 'succeed'
    launch_time: Optional[Union[int, str]] = None  # Unix timestamp or ISO string
    message: str = ""
    provider_api: Optional[str] = None
    provider_name: Optional[str] = None
    provider_type: Optional[str] = None
    resource_id: Optional[str] = None
    request_id: Optional[str] = None
    return_request_id: Optional[str] = None
    cloud_host_id: Optional[str] = None
    price_type: Optional[str] = None
    private_dns_name: Optional[str] = None
    public_dns_name: Optional[str] = None
    metadata: Optional[dict[str, Any]] = Field(default=None)
    health_checks: Optional[dict[str, Any]] = Field(default=None)

    # Additional fields needed by formatter
    template_id: Optional[str] = None
    image_id: Optional[str] = None
    subnet_id: Optional[str] = None
    security_group_ids: Optional[list[str]] = Field(default_factory=list)
    status_reason: Optional[str] = None
    termination_time: Optional[Union[int, str]] = None
    tags: Optional[Any] = None
    provider_data: dict[str, Any] = Field(default_factory=dict)
    version: int = 0

    @classmethod
    def from_domain(cls, machine: Machine, timestamp_format: str = "auto") -> "MachineDTO":
        """Create a fully-populated DTO from a domain Machine.

        All fields are always populated — there is no long/short gate.

        Args:
            machine: Machine domain object.
            timestamp_format: Controls launch_time serialisation.
                "auto" (default) emits a unix int.
                Any other value (e.g. "iso") emits an ISO-8601 string.

        Returns:
            Fully-populated MachineDTO instance.
        """
        status = machine.status.value if hasattr(machine.status, "value") else str(machine.status)

        if machine.launch_time is None:
            launch_time: int | str | None = None
        elif timestamp_format == "auto":
            launch_time = int(machine.launch_time.timestamp())
        else:
            launch_time = machine.launch_time.isoformat()

        provider_data: dict[str, Any] = machine.provider_data if machine.provider_data else {}

        return cls(
            machine_id=str(machine.machine_id),
            name=machine.name or str(machine.machine_id),
            status=status,
            instance_type=str(machine.instance_type),
            private_ip=str(machine.private_ip) if machine.private_ip else "",
            public_ip=str(machine.public_ip) if machine.public_ip else None,
            result=map_machine_status_to_result(status),
            launch_time=launch_time,
            message=machine.metadata.get("message", "") if machine.metadata else "",
            request_id=str(machine.request_id) if machine.request_id else None,
            return_request_id=(
                str(machine.return_request_id) if machine.return_request_id else None
            ),
            provider_name=machine.provider_name,
            provider_type=machine.provider_type,
            provider_api=str(machine.provider_api) if machine.provider_api else None,
            resource_id=str(machine.resource_id) if machine.resource_id else None,
            price_type=machine.price_type if machine.price_type else None,
            cloud_host_id=provider_data.get("cloud_host_id"),
            metadata=machine.metadata,
            health_checks=provider_data.get("health_checks"),
            private_dns_name=machine.private_dns_name,
            public_dns_name=machine.public_dns_name,
            provider_data=provider_data,
            version=machine.version,
            subnet_id=machine.subnet_id,
            security_group_ids=machine.security_group_ids or [],
            template_id=machine.template_id,
            image_id=machine.image_id,
            status_reason=machine.status_reason,
            termination_time=(
                int(machine.termination_time.timestamp())
                if machine.termination_time is not None
                else None
            ),
            tags=machine.tags,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format - returns snake_case for internal use.

        External format conversion should be handled at scheduler strategy level.

        Returns:
            Dictionary representation with snake_case keys
        """
        return super().to_dict()


class MachineHealthDTO(BaseDTO):
    """Data transfer object for machine health."""

    machine_id: str
    overall_status: str
    system_status: str
    instance_status: str
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    last_check: datetime

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format - returns snake_case for internal use.

        External format conversion should be handled at scheduler strategy level.

        Returns:
            Dictionary representation with snake_case keys
        """
        return super().to_dict()
