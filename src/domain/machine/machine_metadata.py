"""Machine metadata, configuration, and monitoring value objects."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import field_validator, model_validator

from domain.base.value_objects import IPAddress, ValueObject


class PriceType(str, Enum):
    """
    Pricing type for EC2 instances (matches template priceType).

    Based on awsprov_templates.md documentation:
    - ondemand: On-Demand instances
    - spot: Spot instances
    - heterogeneous: Mix of On-Demand and Spot instances
    """

    ON_DEMAND = "ondemand"
    SPOT = "spot"
    HETEROGENEOUS = "heterogeneous"

    @classmethod
    def from_string(cls, value: str) -> "PriceType":
        """Create PriceType from string value.

        Args:
            value: Price type string

        Returns:
            PriceType instance

        Raises:
            ValueError: If value is not valid
        """
        if not value:
            return cls.ON_DEMAND  # Default

        normalized = value.lower().strip()
        for price_type in cls:
            if price_type.value == normalized:
                return price_type

        raise ValueError(f"Invalid price type: {value}")


class MachineConfiguration(ValueObject):
    """Machine configuration with validation."""

    instance_type: str
    private_ip: IPAddress
    provider_api: str
    resource_id: str
    public_ip: Optional[IPAddress] = None
    price_type: PriceType = PriceType.ON_DEMAND
    cloud_host_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_configuration(self) -> "MachineConfiguration":
        """Validate machine configuration."""
        if not self.instance_type:
            raise ValueError("Instance type is required")
        if not self.provider_api:
            raise ValueError("Provider API configuration is required")
        if not self.resource_id:
            raise ValueError("Resource ID is required")
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        result = {
            "instanceType": self.instance_type,
            "privateIpAddress": str(self.private_ip),
            "providerApi": self.provider_api,
            "resourceId": self.resource_id,
            "priceType": self.price_type.value,
        }

        if self.public_ip:
            result["publicIpAddress"] = str(self.public_ip)
        if self.cloud_host_id:
            result["cloudHostId"] = self.cloud_host_id

        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MachineConfiguration":
        """Create configuration from dictionary."""
        return cls(
            instance_type=data["instanceType"],
            private_ip=IPAddress(value=data["privateIpAddress"]),
            public_ip=(
                IPAddress(value=data["publicIpAddress"]) if "publicIpAddress" in data else None
            ),
            provider_api=data["providerApi"],
            resource_id=data["resourceId"],
            price_type=PriceType(data.get("priceType", "ondemand")),
            cloud_host_id=data.get("cloudHostId"),
        )


class MachineEvent(ValueObject):
    """Machine lifecycle event."""

    timestamp: datetime
    event_type: str
    old_state: Optional[str]
    new_state: Optional[str]
    details: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_event(self) -> "MachineEvent":
        """Validate machine event."""
        if not self.event_type:
            raise ValueError("Event type is required")
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "eventType": self.event_type,
            "oldState": self.old_state,
            "newState": self.new_state,
            "details": self.details,
        }


class HealthCheck(ValueObject):
    """Machine health check result."""

    check_type: str
    status: bool
    timestamp: datetime
    details: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_health_check(self) -> "HealthCheck":
        """Validate health check."""
        if not self.check_type:
            raise ValueError("Health check type is required")
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Convert health check to dictionary."""
        return {
            "checkType": self.check_type,
            "status": self.status,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details,
        }


class IPAddressRange(ValueObject):
    """
    Value object representing a CIDR block.

    Attributes:
        cidr: The CIDR notation (e.g., 10.0.0.0/16)
    """

    cidr: str

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, v: str) -> str:
        """Validate CIDR format."""
        # Validate CIDR format (e.g., 10.0.0.0/16)
        if not cls._is_valid_cidr(v):
            raise ValueError(f"Invalid CIDR format: {v}")
        return v

    def __str__(self) -> str:
        return self.cidr

    @property
    def network_address(self) -> str:
        """Get the network address part of the CIDR."""
        return self.cidr.split("/")[0]

    @property
    def prefix_length(self) -> int:
        """Get the prefix length part of the CIDR."""
        return int(self.cidr.split("/")[1])

    @staticmethod
    def _is_valid_cidr(cidr: str) -> bool:
        """Validate CIDR format.

        Args:
            cidr: CIDR block string to validate

        Returns:
            True if valid CIDR format, False otherwise
        """
        # Basic CIDR pattern validation
        pattern = r"^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$"

        # First check the pattern
        if not re.match(pattern, cidr):
            return False

        # Validate IP part
        ip = cidr.split("/")[0]
        if not all(0 <= int(octet) <= 255 for octet in ip.split(".")):
            return False

        # Validate prefix length
        prefix = int(cidr.split("/")[1])
        return 0 <= prefix <= 32


class MachineMetadata(ValueObject):
    """
    Value object representing machine metadata.

    Attributes:
        availability_zone: The provider availability zone
        subnet_id: The subnet ID
        vpc_id: The VPC ID
        ami_id: The AMI ID
        ebs_optimized: Whether the instance is EBS optimized
        monitoring: The monitoring state
        tags: Instance tags
    """

    availability_zone: str
    subnet_id: str
    vpc_id: str
    ami_id: str
    ebs_optimized: bool = False
    monitoring: str = "disabled"
    tags: Dict[str, str] = {}

    @model_validator(mode="after")
    def validate_metadata(self) -> "MachineMetadata":
        """Validate machine metadata."""
        if not self.availability_zone:
            raise ValueError("Availability zone is required")
        if not self.subnet_id:
            raise ValueError("Subnet ID is required")
        if not self.vpc_id:
            raise ValueError("VPC ID is required")
        if not self.ami_id:
            raise ValueError("AMI ID is required")
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to dictionary."""
        return {
            "availability_zone": self.availability_zone,
            "subnet_id": self.subnet_id,
            "vpc_id": self.vpc_id,
            "ami_id": self.ami_id,
            "ebs_optimized": self.ebs_optimized,
            "monitoring": self.monitoring,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MachineMetadata":
        """Create metadata from dictionary."""
        return cls(
            availability_zone=data["availability_zone"],
            subnet_id=data["subnet_id"],
            vpc_id=data["vpc_id"],
            ami_id=data["ami_id"],
            ebs_optimized=data.get("ebs_optimized", False),
            monitoring=data.get("monitoring", "disabled"),
            tags=data.get("tags", {}),
        )


class HealthCheckResult(ValueObject):
    """
    Value object representing a health check result.

    Attributes:
        system_status: System status check result
        instance_status: Instance status check result
        timestamp: When the health check was performed
    """

    system_status: bool
    instance_status: bool
    timestamp: datetime
    system_details: Optional[Dict[str, Any]] = None
    instance_details: Optional[Dict[str, Any]] = None

    @property
    def is_healthy(self) -> bool:
        """Check if machine is healthy."""
        return self.system_status and self.instance_status

    def to_dict(self) -> Dict[str, Any]:
        """Convert health check result to dictionary."""
        return {
            "system": {
                "status": self.system_status,
                "details": self.system_details or {},
            },
            "instance": {
                "status": self.instance_status,
                "details": self.instance_details or {},
            },
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HealthCheckResult":
        """Create health check result from dictionary."""
        return cls(
            system_status=data["system"]["status"],
            instance_status=data["instance"]["status"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            system_details=data["system"].get("details"),
            instance_details=data["instance"].get("details"),
        )


class ResourceTag(ValueObject):
    """
    Value object representing a provider resource tag.

    Attributes:
        key: Tag key
        value: Tag value
    """

    key: str
    value: str

    @model_validator(mode="after")
    def validate_tag(self) -> "ResourceTag":
        """Validate resource tag."""
        if not self.key:
            raise ValueError("Tag key is required")

        # Provider tag key validation
        if len(self.key) > 128:
            raise ValueError("Tag key cannot exceed 128 characters")

        # Provider tag value validation
        if len(self.value) > 256:
            raise ValueError("Tag value cannot exceed 256 characters")
        return self

    def to_dict(self) -> Dict[str, str]:
        """Convert tag to dictionary."""
        return {"Key": self.key, "Value": self.value}

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "ResourceTag":
        """Create tag from dictionary."""
        if "Key" in data and "Value" in data:
            return cls(key=data["Key"], value=data["Value"])
        else:
            # Handle case where data is in key-value format
            return [cls(key=k, value=v) for k, v in data.items()]

    @classmethod
    def get_default_tags(cls) -> List["ResourceTag"]:
        """Get basic default tags.

        Returns:
            List of default resource tags
        """
        # Basic default tags without configuration dependency
        return [
            cls(key="Environment", value="default"),
            cls(key="ManagedBy", value="HostFactory"),
        ]
