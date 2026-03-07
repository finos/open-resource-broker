"""Pure domain value objects - immutable domain primitives without infrastructure dependencies."""

import ipaddress
from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Optional


@dataclass(frozen=True)
class ValueObject(ABC):
    """Base class for all value objects - immutable by design."""

    pass


@dataclass(frozen=True)
class ResourceId(ValueObject):
    """Base class for resource identifiers."""

    value: str
    resource_type: ClassVar[str] = "Resource"

    def __post_init__(self) -> None:
        """Validate resource ID value."""
        if not self.value or not self.value.strip():
            raise ValueError("Resource ID cannot be empty")
        # Use object.__setattr__ for frozen dataclass
        object.__setattr__(self, "value", self.value.strip())

    def __str__(self) -> str:
        """Return string representation of resource ID."""
        return self.value

    def __repr__(self) -> str:
        """Developer representation of resource ID."""
        return f"{self.__class__.__name__}('{self.value}')"


@dataclass(frozen=True)
class ResourceQuota(ValueObject):
    """Resource quota information - tracks limits and usage."""

    resource_type: str
    limit: int
    used: int
    available: int

    def __post_init__(self) -> None:
        """Validate quota values."""
        if self.limit < 0:
            raise ValueError("Limit must be non-negative")
        if self.used < 0:
            raise ValueError("Used must be non-negative")
        if self.available < 0:
            raise ValueError("Available must be non-negative")

        # Ensure available = limit - used
        expected_available = self.limit - self.used
        if self.available != expected_available:
            object.__setattr__(self, "available", expected_available)

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


@dataclass(frozen=True)
class IPAddress(ValueObject):
    """IP address value object."""

    value: str

    def __post_init__(self) -> None:
        """Validate IP address format."""
        try:
            ipaddress.ip_address(self.value)
        except ValueError:
            raise ValueError(f"Invalid IP address: {self.value}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class InstanceType(ValueObject):
    """Instance type value object."""

    value: str

    def __post_init__(self) -> None:
        """Validate instance type format."""
        if not self.value or not isinstance(self.value, str):
            raise ValueError("Instance type must be a non-empty string")
        stripped = self.value.strip()
        if not stripped:
            raise ValueError("Instance type must be a non-empty string")
        object.__setattr__(self, "value", stripped)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class InstanceId(ValueObject):
    """Instance identifier value object."""

    value: str

    def __post_init__(self) -> None:
        """Validate instance ID format."""
        if not self.value or not isinstance(self.value, str):
            raise ValueError("Instance ID must be a non-empty string")
        stripped = self.value.strip()
        if not stripped:
            raise ValueError("Instance ID must be a non-empty string")
        object.__setattr__(self, "value", stripped)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Tags(ValueObject):
    """Tags value object for resource tagging."""

    tags: dict[str, str] = field(default_factory=dict)

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


@dataclass(frozen=True)
class ARN(ValueObject):
    """Cloud provider resource name value object (e.g., ARN format)."""

    value: str

    def __post_init__(self) -> None:
        """Validate ARN format."""
        if not self.value or len(self.value.strip()) == 0:
            raise ValueError("Resource ID cannot be empty")

    def __str__(self) -> str:
        return self.value


class PriceType(str, Enum):
    """Price type enumeration."""

    ONDEMAND = "ondemand"
    SPOT = "spot"
    RESERVED = "reserved"
    HETEROGENEOUS = "heterogeneous"
