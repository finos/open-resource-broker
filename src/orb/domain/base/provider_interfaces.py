"""Provider interfaces for domain layer - comprehensive provider abstraction."""

from dataclasses import dataclass
from enum import Enum  # retained for ProviderInstanceState
from typing import Optional, Protocol


class ProviderInstanceState(str, Enum):
    """Provider-agnostic instance states."""

    PENDING = "pending"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    SHUTTING_DOWN = "shutting-down"
    TERMINATED = "terminated"
    # Internal states
    RETURNED = "returned"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProviderResourceTag:
    """Provider-agnostic resource tag."""

    key: str
    value: str

    def __post_init__(self) -> None:
        """Validate tag key and value."""
        if not self.key or len(self.key) > 128:
            raise ValueError("Tag key must be 1-128 characters")
        if len(self.value) > 256:
            raise ValueError("Tag value must be 0-256 characters")

        # Basic validation - providers can add specific rules
        if self.key.startswith("provider:"):
            raise ValueError("Tag keys cannot start with 'provider:'")


@dataclass(frozen=True)
class ProviderResourceIdentifier:
    """Provider-agnostic resource identifier."""

    provider_type: str  # e.g. "aws"
    resource_type: str  # e.g., "instance", "volume", "network"
    identifier: str  # Provider-specific ID
    region: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate resource identifier."""
        if not self.identifier:
            raise ValueError("Resource identifier cannot be empty")
        if not self.resource_type:
            raise ValueError("Resource type cannot be empty")


@dataclass(frozen=True)
class ProviderLaunchTemplate:
    """Provider-agnostic launch template information."""

    template_id: str
    version: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate launch template."""
        if not self.template_id:
            raise ValueError("Template ID cannot be empty")


class ProviderStateMapper(Protocol):
    """Protocol for mapping provider-specific states to domain states."""

    def map_to_domain_state(self, provider_state: str) -> ProviderInstanceState:  # type: ignore[return]
        """Map provider-specific state to domain state."""
        pass

    def map_from_domain_state(self, domain_state: ProviderInstanceState) -> str:  # type: ignore[return]
        """Map domain state to provider-specific state."""
        pass


class ProviderResourceValidator(Protocol):
    """Protocol for provider-specific resource validation."""

    def validate_resource_identifier(self, identifier: str, resource_type: str) -> bool:  # type: ignore[return]
        """Validate provider-specific resource identifier format."""
        pass

    def validate_tag(self, tag: ProviderResourceTag) -> bool:  # type: ignore[return]
        """Validate provider-specific tag constraints."""
        pass

    def validate_launch_template(self, template: ProviderLaunchTemplate) -> bool:  # type: ignore[return]
        """Validate provider-specific launch template format."""
        pass


class ProviderAdapter(Protocol):
    """Main provider adapter interface."""

    @property
    def provider_type(self) -> str:  # type: ignore[return]
        """Get the provider type (e.g. ``"aws"``)."""
        pass

    @property
    def state_mapper(self) -> ProviderStateMapper:  # type: ignore[return]
        """Get the state mapper for this provider."""
        pass

    @property
    def resource_validator(self) -> ProviderResourceValidator:  # type: ignore[return]
        """Get the resource validator for this provider."""
        pass

    def create_resource_identifier(
        self, resource_type: str, identifier: str, region: Optional[str] = None
    ) -> ProviderResourceIdentifier:  # type: ignore[return]
        """Create a provider-specific resource identifier."""
        pass

    def create_launch_template(
        self, template_id: str, version: Optional[str] = None
    ) -> ProviderLaunchTemplate:  # type: ignore[return]
        """Create a provider-specific launch template."""
        pass


# Factory for creating provider adapters
class ProviderAdapterFactory(Protocol):
    """Factory for creating provider adapters."""

    def create_adapter(self, provider_type: str) -> ProviderAdapter:  # type: ignore[return]
        """Create a provider adapter for the specified type (e.g. ``"aws"``)."""
        pass

    def get_supported_providers(self) -> list[str]:  # type: ignore[return]
        """Get list of supported provider type names."""
        pass
