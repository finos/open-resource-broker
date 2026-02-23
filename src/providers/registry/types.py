"""Provider registry types - interfaces and registration data classes."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from infrastructure.registry.base_registry import BaseRegistration


class UnsupportedProviderError(Exception):
    """Exception raised when an unsupported provider type is requested."""


class ProviderFactoryInterface(ABC):
    """Interface for provider factory functions."""

    @abstractmethod
    def create_strategy(self, config: Any) -> Any:
        """Create a provider strategy."""

    @abstractmethod
    def create_config(self, data: dict[str, Any]) -> Any:
        """Create a provider configuration."""


class ProviderRegistration(BaseRegistration):
    """Provider-specific registration with resolver and validator factories."""

    def __init__(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        resolver_factory: Optional[Callable] = None,
        validator_factory: Optional[Callable] = None,
    ) -> None:
        """Initialize the instance."""
        super().__init__(
            type_name,
            strategy_factory,
            config_factory,
            resolver_factory=resolver_factory,
            validator_factory=validator_factory,
        )
        self.resolver_factory = resolver_factory
        self.validator_factory = validator_factory
