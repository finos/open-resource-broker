"""Machine identifiers and core type definitions."""

from __future__ import annotations

import re

from pydantic import field_validator

from domain.base.value_objects import ValueObject


class MachineId(ValueObject):
    """Machine identifier with validation."""

    value: str

    @field_validator("value")
    @classmethod
    def validate_machine_id(cls, v: str) -> str:
        """Validate machine ID format.

        Args:
            v: Machine ID value to validate

        Returns:
            Validated machine ID

        Raises:
            ValueError: If machine ID format is invalid
        """
        # Basic validation for common machine ID patterns
        # AWS: i-1234567890abcdef0 (i- followed by 8-17 hex chars)
        # Generic: any non-empty string for other providers
        if not v or not isinstance(v, str):
            raise ValueError("Machine ID cannot be empty")

        # Allow flexible machine ID formats for different providers
        if len(v.strip()) == 0:
            raise ValueError("Machine ID cannot be empty")

        return v.strip()

    def __str__(self) -> str:
        return self.value


class MachineType(ValueObject):
    """
    Value object representing an EC2 instance type.

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

        # Basic validation for common instance type patterns
        # AWS: t2.micro, m5.large, etc. (family.size)
        # Allow flexible formats for different providers
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
    def from_str(cls, value: str) -> "MachineType":
        """Create instance from string value."""
        return cls(value=value)
