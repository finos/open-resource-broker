"""Data Transfer Objects for machine domain operations."""

from datetime import datetime
from typing import Any, Optional, Union

from pydantic import Field

from orb.application.dto.base import BaseDTO
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.value_objects import MachineStatus


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
    price_type: Optional[str] = None
    private_dns_name: Optional[str] = None
    public_dns_name: Optional[str] = None
    metadata: Optional[dict[str, Any]] = Field(default=None)
    health_checks: Optional[dict[str, Any]] = Field(default=None)
    provider_data: dict[str, Any] = Field(default_factory=dict)
    version: int = 0

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

    @staticmethod
    def _get_result_status(status: str) -> str:
        """Get result status as per HostFactory requirements."""
        if status == MachineStatus.RUNNING.value:
            return "succeed"
        elif status in [MachineStatus.FAILED.value, MachineStatus.TERMINATED.value]:
            return "fail"
        return "executing"

    @classmethod
    def from_domain(cls, machine: Machine, long: bool = False) -> "MachineDTO":
        """
        Create DTO from domain object.

        Args:
            machine: Machine domain object
            long: Whether to include detailed information

        Returns:
            MachineDTO instance
        """
        status = machine.status.value if hasattr(machine.status, "value") else str(machine.status)

        # Common fields for both short and long formats
        common_fields = {
            "machine_id": str(machine.machine_id),
            "name": machine.name,
            "status": status,
            "instance_type": str(machine.instance_type),
            "private_ip": str(machine.private_ip),
            "public_ip": str(machine.public_ip) if machine.public_ip else None,
            "result": cls._get_result_status(status),
            "launch_time": int(machine.launch_time.timestamp()) if machine.launch_time else None,
            "message": machine.metadata.get("message", "") if machine.metadata else "",
            "request_id": str(machine.request_id) if machine.request_id else None,
            "return_request_id": str(machine.return_request_id)
            if machine.return_request_id
            else None,
            "provider_data": machine.provider_data,
            "version": machine.version,
            "subnet_id": machine.subnet_id,
            "security_group_ids": machine.security_group_ids or [],
            "template_id": machine.template_id,
            "image_id": machine.image_id,
            "status_reason": machine.status_reason,
            "termination_time": machine.termination_time,
            "tags": machine.tags,
        }

        # Add additional fields for long format
        if long:
            common_fields.update(
                {
                    "provider_api": (str(machine.provider_api) if machine.provider_api else None),
                    "resource_id": (str(machine.resource_id) if machine.resource_id else None),
                    "price_type": (machine.price_type if machine.price_type else None),
                    "cloud_host_id": machine.provider_data.get("cloud_host_id"),
                    "metadata": machine.metadata,
                    "health_checks": machine.provider_data.get("health_checks"),
                }
            )

        return cls(**common_fields)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary format - returns snake_case for internal use.
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
        """
        Convert to dictionary format - returns snake_case for internal use.
        External format conversion should be handled at scheduler strategy level.

        Returns:
            Dictionary representation with snake_case keys
        """
        return super().to_dict()
