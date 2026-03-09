"""Request identifier value objects."""

from __future__ import annotations

import re
import uuid
from typing import Optional

from pydantic import Field, field_serializer, field_validator

from orb.domain.base.value_objects import ValueObject
from orb.domain.constants import (
    REQUEST_ID_PATTERN,
    REQUEST_ID_PREFIX_ACQUIRE,
    REQUEST_ID_PREFIX_RETURN,
)
from orb.domain.request.request_types import RequestType


class RequestId(ValueObject):
    """
    Request identifier value object.

    Format: {prefix}-{uuid}
    where prefix is:
    - req: for acquire requests
    - ret: for return requests

    Attributes:
        value: The request ID string
    """

    value: str

    @field_validator("value")
    @classmethod
    def validate_request_id(cls, v: str) -> str:
        """Validate request ID format."""
        if not cls._is_valid_format(v):
            raise ValueError(
                f"Invalid request ID format: {v}. Must be in format: req-uuid or ret-uuid"
            )
        return v

    @field_serializer("value")
    @classmethod
    def serialize_value(cls, value: str) -> str:
        """Serialize RequestId as just the string value, not a dict."""
        return value

    def __str__(self) -> str:
        return self.value

    @property
    def request_type(self) -> RequestType:
        """Get the request type from the ID prefix."""
        return (
            RequestType.ACQUIRE
            if self.value.startswith(REQUEST_ID_PREFIX_ACQUIRE)
            else RequestType.RETURN
        )

    @classmethod
    def generate(cls, request_type: RequestType, prefix: Optional[str] = None) -> RequestId:
        """
        Generate a new request ID with appropriate prefix.

        Args:
            request_type: Type of request to generate ID for
            prefix: Optional explicit prefix override

        Returns:
            New RequestId instance
        """
        if prefix is None:
            prefix = (
                REQUEST_ID_PREFIX_ACQUIRE
                if request_type == RequestType.ACQUIRE
                else REQUEST_ID_PREFIX_RETURN
            )
        return cls(value=f"{prefix}{uuid.uuid4()!s}")

    @staticmethod
    def _is_valid_format(value: str) -> bool:
        """Check if value matches required format."""
        return bool(re.match(REQUEST_ID_PATTERN, value))

    @classmethod
    def is_request_id(cls, value: str) -> bool:
        """Check if value is an acquire request ID."""
        return value.startswith(REQUEST_ID_PREFIX_ACQUIRE)

    @classmethod
    def is_return_id(cls, value: str) -> bool:
        """Check if value is a return request ID."""
        return value.startswith(REQUEST_ID_PREFIX_RETURN)


class MachineReference(ValueObject):
    """
    Reference to a machine within a request.

    Attributes:
        machine_id: Unique identifier for the machine
        status: Current status of the machine
        result: Result of the machine operation
        error_message: Error message if operation failed (optional)
        created_at: When the machine reference was created
        updated_at: When the machine reference was last updated
    """

    machine_id: str
    status: str
    result: str
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @field_validator("machine_id")
    @classmethod
    def validate_machine_id(cls, v: str) -> str:
        """Validate machine ID format."""
        if not v or not isinstance(v, str):
            raise ValueError("Machine ID must be a non-empty string")

        if len(v) < 3:
            raise ValueError("Machine ID must be at least 3 characters long")

        return v

    def __str__(self) -> str:
        return f"MachineRef({self.machine_id}, {self.status})"

    def is_successful(self) -> bool:
        """Check if the machine operation was successful."""
        from .request_types import MachineResult

        try:
            result = MachineResult.from_str(self.result)
            return result.is_successful()
        except ValueError:
            return False

    def is_failed(self) -> bool:
        """Check if the machine operation failed."""
        from .request_types import MachineResult

        try:
            result = MachineResult.from_str(self.result)
            return result == MachineResult.FAILED
        except ValueError:
            return False

    def has_error(self) -> bool:
        """Check if the machine has an error message."""
        return self.error_message is not None and self.error_message.strip() != ""

    def update_status(
        self, new_status: str, result: str, error_message: Optional[str] = None
    ) -> MachineReference:
        """Create a new MachineReference with updated status."""
        from datetime import datetime

        return MachineReference(
            machine_id=self.machine_id,
            status=new_status,
            result=result,
            error_message=error_message,
            created_at=self.created_at,
            updated_at=datetime.utcnow().isoformat(),
        )


class ResourceIdentifier(ValueObject):
    """
    Identifier for cloud resources associated with a request.

    This represents various cloud resources that are created or managed
    as part of a request (e.g., launch templates, security groups, etc.).

    Attributes:
        resource_type: Type of the resource (e.g., 'launch_template', 'security_group')
        resource_id: Cloud provider resource ID
        resource_arn: Cloud provider resource ARN (optional)
        region: Cloud region where resource exists (optional)
        tags: Additional tags associated with the resource
    """

    resource_type: str
    resource_id: str
    resource_arn: Optional[str] = None
    region: Optional[str] = None
    tags: dict = Field(default_factory=dict)

    @field_validator("resource_type")
    @classmethod
    def validate_resource_type(cls, v: str) -> str:
        """Validate resource type."""
        if not v or not isinstance(v, str):
            raise ValueError("Resource type must be a non-empty string")

        # Normalize to lowercase with underscores
        normalized = v.lower().replace("-", "_").replace(" ", "_")

        # Basic validation of allowed characters
        if not re.match(r"^[a-z_][a-z0-9_]*$", normalized):
            raise ValueError("Resource type must contain only letters, numbers, and underscores")

        return normalized

    @field_validator("resource_id")
    @classmethod
    def validate_resource_id(cls, v: str) -> str:
        """Validate resource ID."""
        if not v or not isinstance(v, str):
            raise ValueError("Resource ID must be a non-empty string")

        return v.strip()

    def __str__(self) -> str:
        return f"{self.resource_type}:{self.resource_id}"

    def is_arn_resource(self) -> bool:
        """Check if this resource has an ARN identifier."""
        return self.resource_arn is not None and len(self.resource_arn.strip()) > 0

    def get_resource_name(self) -> str:
        """Get a human-readable resource name."""
        return f"{self.resource_type.replace('_', ' ').title()} ({self.resource_id})"
