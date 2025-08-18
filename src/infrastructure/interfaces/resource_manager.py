"""Core resource manager interface - provider-agnostic resource management."""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from domain.base.value_objects import ResourceId


class ResourceConfig(BaseModel):
    """Base configuration for cloud resources."""

    model_config = ConfigDict(extra="allow")  # Allow resource-specific config fields

    resource_type: str
    name: Optional[str] = None
    tags: Dict[str, str] = {}


@runtime_checkable
class ResourceManagerPort(Protocol):
    """Interface for managing cloud resources."""

    def create_resource(self, config: ResourceConfig) -> ResourceId:
        """Create a cloud resource."""
        ...

    def delete_resource(self, resource_id: ResourceId) -> bool:
        """Delete a cloud resource."""
        ...

    def get_resource_status(self, resource_id: ResourceId) -> str:
        """Get the status of a cloud resource."""
        ...

    def get_resource_details(self, resource_id: ResourceId) -> Dict[str, Any]:
        """Get detailed information about a cloud resource."""
        ...

    def list_resources(self, resource_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """List cloud resources, optionally filtered by type."""
        ...

    def update_resource_tags(self, resource_id: ResourceId, tags: Dict[str, str]) -> bool:
        """Update tags on a cloud resource."""
        ...
