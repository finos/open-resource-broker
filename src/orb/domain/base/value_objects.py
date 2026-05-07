"""Base value objects - immutable domain primitives."""

import ipaddress
from abc import ABC
from enum import Enum
from typing import ClassVar, Optional, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_serializer,
    model_validator,
)

T = TypeVar("T", bound="ValueObject")


class ValueObject(BaseModel, ABC):
    """Base class for all value objects.

    Subclasses that define ``@model_serializer`` serialize to a **single scalar
    shape** (e.g. ``ResourceId`` → ``str``, ``Tags`` → ``dict``).  This is
    intentional: Pydantic's ``model_dump`` / ``model_validate`` round-trip works
    correctly because the matching ``@model_validator(mode="before")`` accepts
    that same scalar on the way back in.

    Constraint — if a second serialization shape is ever needed (e.g. a verbose
    ``{"value": ..., "meta": ...}`` form alongside the plain-scalar form), do
    **not** add a second ``@model_serializer`` branch here.  Instead, move the
    alternative serialization to the infrastructure layer (e.g. a dedicated
    ``to_dict`` method on the repository serializer or DTO factory) so the
    domain value object stays single-purpose.
    """

    model_config = ConfigDict(
        frozen=True,  # Value objects are immutable
        validate_assignment=True,
        arbitrary_types_allowed=True,
    )


class ResourceId(ValueObject):
    """Base class for resource identifiers."""

    value: str
    resource_type: ClassVar[str] = "Resource"

    @model_validator(mode="before")
    @classmethod
    def _accept_string(cls, data: object) -> object:
        """Allow construction from a plain string, e.g. model_validate('i-abc')."""
        if isinstance(data, str):
            return {"value": data}
        return data

    @model_serializer
    def _serialize(self) -> str:
        """Serialize to a plain string instead of {'value': '...'}."""
        return self.value

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

    @model_validator(mode="before")
    @classmethod
    def _accept_string(cls, data: object) -> object:
        """Allow construction from a plain string, e.g. model_validate('m5.large')."""
        if isinstance(data, str):
            return {"value": data}
        return data

    @model_serializer
    def _serialize(self) -> str:
        """Serialize to a plain string instead of {'value': '...'}."""
        return self.value

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


class InstanceId(ValueObject):
    """Instance identifier value object."""

    value: str

    @model_validator(mode="before")
    @classmethod
    def _accept_string(cls, data: object) -> object:
        """Allow construction from a plain string, e.g. model_validate('i-abc123')."""
        if isinstance(data, str):
            return {"value": data}
        return data

    @model_serializer
    def _serialize(self) -> str:
        """Serialize to a plain string instead of {'value': '...'}."""
        return self.value

    def __str__(self) -> str:
        return self.value

    @field_validator("value")
    @classmethod
    def validate_instance_id(cls, v: str) -> str:
        """Validate instance ID format."""
        if not v or not isinstance(v, str):
            raise ValueError("Instance ID must be a non-empty string")
        stripped = v.strip()
        if not stripped:
            raise ValueError("Instance ID must be a non-empty string")
        return stripped


class Tags(ValueObject):
    """Tags value object for resource tagging."""

    tags: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _accept_dict(cls, data: object) -> object:
        """Allow construction from a plain dict, e.g. model_validate({'Env': 'prod'})."""
        if isinstance(data, dict) and "tags" not in data:
            return {"tags": data}
        return data

    @model_serializer
    def _serialize(self) -> dict[str, str]:
        """Serialize to a plain dict instead of {'tags': {...}}."""
        return dict(self.tags)

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
    """Cloud provider resource name value object (e.g., ARN format)."""

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
