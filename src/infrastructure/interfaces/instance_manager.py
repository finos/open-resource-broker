"""Core instance manager interface - provider-agnostic instance management."""

from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from domain.base.value_objects import InstanceId, InstanceType, Tags


class InstanceState(str, Enum):
    """Instance state enumeration."""

    PENDING = "pending"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    SHUTTING_DOWN = "shutting-down"
    TERMINATED = "terminated"


class InstanceSpec(BaseModel):
    """Specification for creating instances."""

    model_config = ConfigDict(extra="allow")  # Allow provider-specific config fields

    instance_type: InstanceType
    image_id: str
    count: int = 1
    tags: Optional[Tags] = None
    subnet_id: Optional[str] = None
    security_group_ids: Optional[List[str]] = None
    key_name: Optional[str] = None
    user_data: Optional[str] = None


class Instance(BaseModel):
    """Instance information."""

    model_config = ConfigDict(extra="allow")  # Allow provider-specific fields

    instance_id: InstanceId
    instance_type: InstanceType
    state: InstanceState
    image_id: str
    launch_time: Optional[str] = None
    tags: Optional[Tags] = None


class InstanceStatusResponse(BaseModel):
    """Response for instance status queries."""

    model_config = ConfigDict(extra="allow")

    instances: List[Instance]
    total_count: int


class InstanceConfig(BaseModel):
    """Base configuration for cloud instances."""

    model_config = ConfigDict(extra="allow")  # Allow provider-specific config fields

    instance_type: InstanceType
    image_id: str
    count: int = 1
    tags: Optional[Tags] = None


@runtime_checkable
class InstanceManagerPort(Protocol):
    """Interface for managing cloud instances."""

    def launch_instances(self, config: InstanceConfig) -> List[InstanceId]:
        """Launch cloud instances."""
        ...

    def terminate_instances(self, instance_ids: List[InstanceId]) -> bool:
        """Terminate cloud instances."""
        ...

    def get_instance_status(self, instance_ids: List[InstanceId]) -> InstanceStatusResponse:
        """Get status of specific instances."""
        ...

    def create_instances(self, spec: InstanceSpec) -> List[Instance]:
        """Create instances based on specification."""
        ...

    def list_instances(self, filters: Optional[Dict[str, Any]] = None) -> List[Instance]:
        """List instances with optional filters."""
        ...

    def stop_instances(self, instance_ids: List[InstanceId]) -> bool:
        """Stop cloud instances (if supported by provider)."""
        ...

    def start_instances(self, instance_ids: List[InstanceId]) -> bool:
        """Start stopped cloud instances (if supported by provider)."""
        ...

    def get_instance_details(self, instance_ids: List[InstanceId]) -> List[Dict[str, Any]]:
        """Get detailed information about cloud instances."""
        ...

    def update_instance_tags(self, instance_ids: List[InstanceId], tags: Tags) -> bool:
        """Update tags on cloud instances."""
        ...
