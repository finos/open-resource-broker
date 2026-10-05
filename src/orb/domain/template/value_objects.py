"""Template value objects - provider-agnostic domain logic."""

# Import core domain value objects
from typing import Any, Protocol

from orb.domain.base.value_objects import ResourceId

# Export all public classes
__all__: list[str] = ["ProviderConfiguration", "TemplateId"]


class TemplateId(ResourceId):
    """Template identifier."""

    resource_type = "Template"


# Provider-agnostic contracts (Protocols)
class FleetTypePort(Protocol):
    """Contract for provider-specific fleet type implementations."""

    def get_valid_types_for_handler(self, handler_type: "ProviderHandlerTypePort") -> list[str]:  # type: ignore[return]
        """Get valid fleet types for a specific handler type."""
        pass

    def get_default_for_handler(self, handler_type: "ProviderHandlerTypePort") -> str:  # type: ignore[return]
        """Get default fleet type for a specific handler type."""
        pass

    def validate_for_handler(self, handler_type: "ProviderHandlerTypePort") -> bool:  # type: ignore[return]
        """Validate if this fleet type is supported by the handler type."""
        pass


class ProviderHandlerTypePort(Protocol):
    """Contract for provider-specific handler type implementations."""

    def validate(self, value: str) -> bool:  # type: ignore[return]
        """Validate if the handler type value is supported."""
        pass

    def get_supported_types(self) -> list[str]:  # type: ignore[return]
        """Get all supported handler type values."""
        pass


# Provider-agnostic configuration
class ProviderConfiguration:
    """Provider-agnostic configuration container."""

    def __init__(self, config_data: dict[str, Any]) -> None:
        """Initialize the instance."""
        self.config_data = config_data

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self.config_data.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return self.config_data.copy()
