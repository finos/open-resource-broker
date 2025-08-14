"""Data Transfer Objects for machine domain operations."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field

from src.application.dto.base import BaseDTO
from src.domain.machine.aggregate import Machine
from src.domain.machine.value_objects import MachineStatus


class MachineDTO(BaseDTO):
    """DTO for machine responses."""

    machine_id: str
    name: str
    status: str
    instance_type: str
    private_ip: str
    public_ip: Optional[str] = None
    result: str  # 'executing', 'fail', or 'succeed'
    launch_time: int
    message: str = ""
    provider_api: Optional[str] = None
    resource_id: Optional[str] = None
    price_type: Optional[str] = None
    cloud_host_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default=None)
    health_checks: Optional[Dict[str, Any]] = Field(default=None)

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
            "launch_time": int(machine.launch_time.timestamp()),
            "message": machine.message,
        }

        # Add additional fields for long format
        if long:
            common_fields.update(
                {
                    "provider_api": (str(machine.provider_api) if machine.provider_api else None),
                    "resource_id": (str(machine.resource_id) if machine.resource_id else None),
                    "price_type": (
                        machine.price_type.value
                        if hasattr(machine.price_type, "value")
                        else str(machine.price_type)
                    ),
                    "cloud_host_id": machine.cloud_host_id,
                    "metadata": machine.metadata,
                    "health_checks": machine.health_checks,
                }
            )

        return cls(**common_fields)

    def to_dict(self) -> Dict[str, Any]:
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
    metrics: List[Dict[str, Any]] = Field(default_factory=list)
    last_check: datetime

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary format - returns snake_case for internal use.
        External format conversion should be handled at scheduler strategy level.

        Returns:
            Dictionary representation with snake_case keys
        """
        return super().to_dict()
