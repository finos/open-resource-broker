"""Port interface for provider registry operations used by the application layer."""

from abc import ABC, abstractmethod
from typing import Any, Optional

from orb.domain.base.results import ProviderSelectionResult
from orb.domain.template.template_aggregate import Template


class ProviderRegistryPort(ABC):
    """Port that the application layer uses to interact with the provider registry.

    Concrete implementations live in the providers layer (ProviderRegistry).
    This breaks the direct application→providers.registry import dependency.
    """

    @abstractmethod
    def select_provider_for_template(
        self,
        template: Template,
        provider_name: Optional[str],
        logger: Optional[Any] = None,
    ) -> ProviderSelectionResult:
        """Select a provider for the given template."""
        pass

    @abstractmethod
    def select_active_provider(self, logger: Optional[Any] = None) -> ProviderSelectionResult:
        """Select the currently active provider."""
        pass

    @abstractmethod
    def get_or_create_strategy(self, provider_identifier: str, config: Any = None) -> Optional[Any]:
        """Get or create a provider strategy by identifier."""
        pass

    @abstractmethod
    def get_registered_providers(self) -> list[str]:
        """Return list of registered provider type names."""
        pass

    @abstractmethod
    def get_registered_provider_instances(self) -> list[str]:
        """Return list of registered provider instance names."""
        pass

    @abstractmethod
    def ensure_provider_type_registered(self, provider_type: str) -> bool:
        """Ensure a provider type is registered, registering it if needed."""
        pass

    @abstractmethod
    def update_provider_health(self, provider_name: str, health_data: dict) -> None:
        """Persist health state for a provider."""
        pass

    @abstractmethod
    def get_config_factory(self, provider_type: str) -> Optional[Any]:
        """Return the config_factory callable for the given provider type, or None if not registered."""
        pass

    @abstractmethod
    def is_provider_registered(self, provider_type: str) -> bool:
        """Check if a provider type is registered."""
        pass  # type: ignore[return]

    @abstractmethod
    def is_provider_instance_registered(self, instance_name: str) -> bool:
        """Check if a provider instance is registered."""
        pass  # type: ignore[return]

    @abstractmethod
    def ensure_provider_instance_registered_from_config(self, provider_instance: Any) -> bool:
        """Ensure provider instance is registered from config."""
        pass  # type: ignore[return]

    @abstractmethod
    def create_strategy_by_type(self, provider_type: str, config: Any = None) -> Any:
        """Create a provider strategy directly by type, bypassing the instance cache."""
        pass  # type: ignore[return]

    @abstractmethod
    def create_validator(self, provider_type: str) -> Optional[Any]:
        """Create a template validator using the registered factory for the given provider type."""
        pass  # type: ignore[return]
