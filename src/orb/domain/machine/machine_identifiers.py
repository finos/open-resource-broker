"""Machine identifiers and core type definitions."""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import field_validator

from orb.domain.base.value_objects import ResourceId, ValueObject


class MachineId(ResourceId):
    """Machine identifier with validation.

    Inherits value validation and self-flattening serialization from ResourceId:
    - model_dump() returns a plain string (e.g. 'i-abc'), not {'value': 'i-abc'}
    - model_validate('i-abc') constructs MachineId(value='i-abc')
    - model_validate({'value': 'i-abc'}) also works for backward compatibility
    """

    resource_type: ClassVar[str] = "Machine"

    def __str__(self) -> str:
        return self.value


class MachineType(ValueObject):
    """
    Value object representing a compute instance type.

    Attributes:
        value: The instance type identifier (e.g., t2.micro, m5.large)
    """

    value: str

    @field_validator("value")
    @classmethod
    def validate_instance_type(cls, v: str) -> str:
        """Validate instance type format.

        Args:
            v: Instance type value to validate

        Returns:
            Validated instance type

        Raises:
            ValueError: If instance type format is invalid
        """
        if not v or not isinstance(v, str):
            raise ValueError("Instance type cannot be empty")

        # Basic validation for common instance type patterns (family.size)
        # Supports flexible formats across different providers
        if not re.match(r"^[a-zA-Z0-9]+\.[a-zA-Z0-9]+$", v):
            raise ValueError(f"Invalid instance type format: {v}")

        return v

    def __str__(self) -> str:
        return self.value

    @property
    def family(self) -> str:
        """Get the instance family (e.g., t2, m5)."""
        return self.value.split(".")[0]

    @property
    def size(self) -> str:
        """Get the instance size (e.g., micro, large)."""
        return self.value.split(".")[1]

    @classmethod
    def from_str(cls, value: str) -> MachineType:
        """Create instance from string value."""
        return cls(value=value)
