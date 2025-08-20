"""Template value objects - provider-agnostic domain logic."""

# Import core domain value objects
from typing import Any, Dict, List, Protocol

from domain.base.value_objects import ResourceId

# Export all public classes
__all__: list[str] = ["TemplateId", "ProviderConfiguration"]


class TemplateId(ResourceId):
    """Template identifier."""

    resource_type = "Template"


# Provider-agnostic contracts (Protocols)
class FleetTypePort(Protocol):
    """Contract for provider-specific fleet type implementations."""

    def get_valid_types_for_handler(self, handler_type: "ProviderHandlerTypePort") -> List[str]:
        """Get valid fleet types for a specific handler type."""
        ...

    def get_default_for_handler(self, handler_type: "ProviderHandlerTypePort") -> str:
        """Get default fleet type for a specific handler type."""
        ...

    def validate_for_handler(self, handler_type: "ProviderHandlerTypePort") -> bool:
        """Validate if this fleet type is supported by the handler type."""
        ...


class ProviderHandlerTypePort(Protocol):
    """Contract for provider-specific handler type implementations."""

    def validate(self, value: str) -> bool:
        """Validate if the handler type value is supported."""
        ...

    def get_supported_types(self) -> List[str]:
        """Get all supported handler type values."""
        ...


# Provider-agnostic configuration
class ProviderConfiguration:
    """Provider-agnostic configuration container."""

    def __init__(self, config_data: Dict[str, Any]) -> None:
        """Initialize the instance."""
        self.config_data = config_data

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self.config_data.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return self.config_data.copy()
