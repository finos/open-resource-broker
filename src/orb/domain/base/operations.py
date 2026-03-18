"""Domain-level operation types and value objects.

This module defines provider-agnostic operation types that belong in the domain layer.
Infrastructure implementations can map these to concrete provider operations.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class OperationType(str, Enum):
    """Types of operations that can be executed via providers."""

    CREATE_INSTANCES = "create_instances"
    TERMINATE_INSTANCES = "terminate_instances"
    GET_INSTANCE_STATUS = "get_instance_status"
    DESCRIBE_RESOURCE_INSTANCES = "describe_resource_instances"
    VALIDATE_TEMPLATE = "validate_template"
    GET_AVAILABLE_TEMPLATES = "get_available_templates"
    HEALTH_CHECK = "health_check"
    RESOLVE_IMAGE = "resolve_image"
    START_INSTANCES = "start_instances"
    STOP_INSTANCES = "stop_instances"


@dataclass
class Operation:
    """Value object representing a provider operation to be executed.

    This is a domain-level abstraction that doesn't depend on infrastructure.
    """

    operation_type: OperationType
    parameters: dict[str, Any]
    context: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        """Validate operation parameters after initialization."""
        if not isinstance(self.parameters, dict):
            raise ValueError("Operation parameters must be a dictionary")

        if self.context is not None and not isinstance(self.context, dict):
            raise ValueError("Operation context must be a dictionary or None")


@dataclass
class OperationResult:
    """Value object representing the result of an operation.

    This is a domain-level abstraction for operation results.
    """

    success: bool
    data: Any = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    metadata: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Initialize metadata if not provided."""
        if self.metadata is None:
            self.metadata = {}

    @classmethod
    def success_result(
        cls, data: Any = None, metadata: Optional[dict[str, Any]] = None
    ) -> "OperationResult":
        """Create a successful result."""
        return cls(success=True, data=data, metadata=metadata or {})

    @classmethod
    def error_result(
        cls,
        error_message: str,
        error_code: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> "OperationResult":
        """Create an error result."""
        return cls(
            success=False,
            error_message=error_message,
            error_code=error_code,
            metadata=metadata or {},
        )
