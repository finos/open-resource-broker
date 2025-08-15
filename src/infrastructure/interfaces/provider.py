"""Core provider interfaces - contracts that all providers must implement."""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from domain.base.value_objects import InstanceId


class BaseProviderConfig(BaseModel):
    """Base configuration for providers."""

    model_config = ConfigDict(extra="allow")  # Allow provider-specific config fields

    provider_type: str
    region: Optional[str] = None


# Alias for backward compatibility
ProviderConfig = BaseProviderConfig


@runtime_checkable
class ProviderPort(Protocol):
    """Core interface that all providers must implement."""

    @property
    def provider_type(self) -> str:
        """Get the provider type."""
        ...

    def initialize(self, config: BaseProviderConfig) -> bool:
        """Initialize the provider with configuration."""
        ...

    def create_instances(self, template_config: Dict[str, Any], count: int) -> List[InstanceId]:
        """Create instances based on template configuration."""
        ...

    def terminate_instances(self, instance_ids: List[InstanceId]) -> bool:
        """Terminate the specified instances."""
        ...

    def get_instance_status(self, instance_ids: List[InstanceId]) -> Dict[InstanceId, str]:
        """Get the current status of the specified instances."""
        ...

    def validate_template(self, template_config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a template configuration for this provider."""
        ...

    def get_available_templates(self) -> List[Dict[str, Any]]:
        """Get available templates for this provider."""
        ...
