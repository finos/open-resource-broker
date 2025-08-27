"""Base value objects - immutable domain primitives."""

import ipaddress
from abc import ABC
from enum import Enum
from typing import ClassVar, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

T = TypeVar("T", bound="ValueObject")


class ValueObject(BaseModel, ABC):
    """Base class for all value objects."""

    model_config = ConfigDict(
        frozen=True,  # Value objects are immutable
        validate_assignment=True,
        arbitrary_types_allowed=True,
    )


class ResourceId(ValueObject):
    """Base class for resource identifiers."""

    value: str
    resource_type: ClassVar[str] = "Resource"

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: str) -> str:
        """Validate resource ID value.

        Args:
            v: Resource ID value to validate

        Returns:
            Validated and trimmed resource ID

        Raises:
            ValueError: If resource ID is empty or whitespace only
        """
        if not v or not v.strip():
            raise ValueError("Resource ID cannot be empty")
        return v.strip()

    def __str__(self) -> str:
        """Return string representation of resource ID.

        Returns:
            Resource ID value as string
        """
        return self.value

    def __repr__(self) -> str:
        """Developer representation of resource ID.

        Returns:
            Formatted representation showing class and value
        """
        return f"{self.__class__.__name__}('{self.value}')"


class ResourceQuota(ValueObject):
    """Resource quota information - tracks limits and usage."""

    resource_type: str = Field(..., description="Type of resource (e.g., 'instances', 'volumes')")
    limit: int = Field(..., ge=0, description="Maximum allowed resources")
    used: int = Field(..., ge=0, description="Currently used resources")
    available: int = Field(..., ge=0, description="Available resources")

    @field_validator("available")
    @classmethod
    def validate_available(cls, v: int, info: ValidationInfo) -> int:
        """Ensure available = limit - used."""
        if "limit" in info.data and "used" in info.data:
            expected_available = info.data["limit"] - info.data["used"]
            if v != expected_available:
                return expected_available
        return v

    @property
    def utilization_percentage(self) -> float:
        """Calculate utilization as a percentage."""
        if self.limit == 0:
            return 0.0
        return (self.used / self.limit) * 100.0

    @property
    def is_at_limit(self) -> bool:
        """Check if resource is at its limit."""
        return self.used >= self.limit

    def __str__(self) -> str:
        return (
            f"{self.resource_type}: {self.used}/{self.limit} ({self.utilization_percentage:.1f}%)"
        )


class InstanceId(ResourceId):
    """Instance identifier value object."""

    resource_type: ClassVar[str] = "Instance"


class IPAddress(ValueObject):
    """IP address value object."""

    value: str

    @field_validator("value")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        """Validate IP address format."""
        try:
            ipaddress.ip_address(v)
            return v
        except ValueError:
            raise ValueError(f"Invalid IP address: {v}")

    def __str__(self) -> str:
        return self.value


class InstanceType(ValueObject):
    """Instance type value object."""

    value: str

    def __str__(self) -> str:
        return self.value

    @field_validator("value")
    @classmethod
    def validate_instance_type(cls, v: str) -> str:
        """Validate instance type format."""
        if not v or not isinstance(v, str):
            raise ValueError("Instance type must be a non-empty string")
        stripped = v.strip()
        if not stripped:
            raise ValueError("Instance type must be a non-empty string")
        return stripped


class Tags(ValueObject):
    """Tags value object for resource tagging."""

    tags: dict[str, str] = Field(default_factory=dict)

    def __str__(self) -> str:
        if not self.tags:
            return "{}"
        return str(self.tags)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get tag value by key."""
        return self.tags.get(key, default)

    def add(self, key: str, value: str) -> "Tags":
        """Add a tag (returns new Tags instance)."""
        new_tags = self.tags.copy()
        new_tags[key] = value
        return Tags(tags=new_tags)

    def remove(self, key: str) -> "Tags":
        """Remove a tag (returns new Tags instance)."""
        new_tags = self.tags.copy()
        new_tags.pop(key, None)
        return Tags(tags=new_tags)

    def to_dict(self) -> dict[str, str]:
        """Convert tags to dictionary."""
        return dict(self.tags)

    @classmethod
    def from_dict(cls, tags_dict: dict[str, str]) -> "Tags":
        """Create Tags from dictionary."""
        return cls(tags=tags_dict)

    def merge(self, other: "Tags") -> "Tags":
        """Merge with another Tags instance (returns new Tags instance)."""
        merged_tags = self.tags.copy()
        merged_tags.update(other.tags)
        return Tags(tags=merged_tags)


class ARN(ValueObject):
    """Amazon Resource Name value object."""

    value: str

    def __str__(self) -> str:
        return self.value

    @field_validator("value")
    @classmethod
    def validate_arn_format(cls, v: str) -> str:
        """Validate ARN format."""
        # Basic resource identifier format validation
        # Provider-specific validation should be done in provider layers
        if not v or len(v.strip()) == 0:
            raise ValueError("Resource ID cannot be empty")
        return v


class PriceType(str, Enum):
    """Price type enumeration."""

    ONDEMAND = "ondemand"
    SPOT = "spot"
    RESERVED = "reserved"
    HETEROGENEOUS = "heterogeneous"  # Mix of different pricing types


class AllocationStrategy(str, Enum):
    """Allocation strategy enumeration."""

    LOWEST_PRICE = "lowestPrice"
    DIVERSIFIED = "diversified"
    CAPACITY_OPTIMIZED = "capacityOptimized"
    CAPACITY_OPTIMIZED_PRIORITIZED = "capacityOptimizedPrioritized"
