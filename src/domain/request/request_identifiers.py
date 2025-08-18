"""Request identifier value objects."""

from __future__ import annotations

import re
import uuid
from typing import Optional

from pydantic import field_validator

from domain.base.value_objects import ValueObject
from domain.request.request_types import RequestType


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

    def __str__(self) -> str:
        return self.value

    @property
    def request_type(self) -> RequestType:
        """Get the request type from the ID prefix."""
        prefix = self.value.split("-")[0]
        return RequestType.ACQUIRE if prefix == "req" else RequestType.RETURN

    @classmethod
    def generate(cls, request_type: RequestType) -> "RequestId":
        """
        Generate a new request ID with appropriate prefix.

        Args:
            request_type: Type of request to generate ID for

        Returns:
            New RequestId instance
        """
        try:
            from domain.base.configuration_service import get_domain_config_service

            config_service = get_domain_config_service()
            if config_service:
                prefix = config_service.get_request_id_prefix(request_type.value)
            else:
                # Fallback if service not available
                prefix = "req-" if request_type == RequestType.ACQUIRE else "ret-"
        except ImportError:
            # Fallback if service not available
            prefix = "req-" if request_type == RequestType.ACQUIRE else "ret-"

        return cls(value=f"{prefix}{str(uuid.uuid4())}")

    @staticmethod
    def _is_valid_format(value: str) -> bool:
        """Check if value matches required format."""
        try:
            from domain.base.configuration_service import get_domain_config_service

            config_service = get_domain_config_service()
            if config_service:
                pattern = config_service.get_request_id_pattern()
            else:
                # Fallback pattern if service not available
                pattern = r"^(req-|ret-)[a-f0-9\-]{36}$"
        except ImportError:
            # Fallback pattern if service not available
            pattern = r"^(req-|ret-)[a-f0-9\-]{36}$"

        return bool(re.match(pattern, value))


class MachineReference(ValueObject):
    """
    Reference to a machine within a request.

    This represents a machine that is part of a request, including its
    current status and any associated metadata.

    Attributes:
        machine_id: Unique identifier for the machine
        instance_id: Cloud provider instance ID (optional)
        status: Current status of the machine
        result: Result of the machine operation
        error_message: Error message if operation failed (optional)
        created_at: When the machine reference was created
        updated_at: When the machine reference was last updated
    """

    machine_id: str
    instance_id: Optional[str] = None
    status: str  # MachineStatus enum value as string
    result: str  # MachineResult enum value as string
    error_message: Optional[str] = None
    created_at: Optional[str] = None  # ISO format datetime string
    updated_at: Optional[str] = None  # ISO format datetime string

    @field_validator("machine_id")
    @classmethod
    def validate_machine_id(cls, v: str) -> str:
        """Validate machine ID format."""
        if not v or not isinstance(v, str):
            raise ValueError("Machine ID must be a non-empty string") from e

        # Basic format validation - can be extended based on requirements
        if len(v) < 3:
            raise ValueError("Machine ID must be at least 3 characters long") from e

        return v

    @field_validator("instance_id")
    @classmethod
    def validate_instance_id(cls, v: Optional[str]) -> Optional[str]:
        """Validate instance ID format if provided."""
        if v is None:
            return v

        if not isinstance(v, str) or not v.strip():
            raise ValueError("Instance ID must be a non-empty string if provided") from e

        return v.strip()

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
    ) -> "MachineReference":
        """
        Create a new MachineReference with updated status.

        Args:
            new_status: New machine status
            result: New machine result
            error_message: Optional error message

        Returns:
            New MachineReference instance with updated values
        """
        from datetime import datetime

        return MachineReference(
            machine_id=self.machine_id,
            instance_id=self.instance_id,
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
    tags: dict = {}

    @field_validator("resource_type")
    @classmethod
    def validate_resource_type(cls, v: str) -> str:
        """Validate resource type."""
        if not v or not isinstance(v, str):
            raise ValueError("Resource type must be a non-empty string") from e

        # Normalize to lowercase with underscores
        normalized = v.lower().replace("-", "_").replace(" ", "_")

        # Basic validation of allowed characters
        if not re.match(r"^[a-z_][a-z0-9_]*$", normalized):
            raise ValueError("Resource type must contain only letters, numbers, and underscores") from e

        return normalized

    @field_validator("resource_id")
    @classmethod
    def validate_resource_id(cls, v: str) -> str:
        """Validate resource ID."""
        if not v or not isinstance(v, str):
            raise ValueError("Resource ID must be a non-empty string") from e

        return v.strip()

    def __str__(self) -> str:
        return f"{self.resource_type}:{self.resource_id}"

    def is_aws_resource(self) -> bool:
        """Check if this is an AWS resource based on ARN."""
        return self.resource_arn is not None and self.resource_arn.startswith("arn:aws:")

    def get_resource_name(self) -> str:
        """Get a human-readable resource name."""
        return f"{self.resource_type.replace('_', ' ').title()} ({self.resource_id})"
