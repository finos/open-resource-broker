"""Provider registry types - interfaces and registration data classes."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from orb.infrastructure.registry.base_registry import BaseRegistration


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
        strategy_class: Optional[type] = None,
        default_api: Optional[str] = None,
        display_name: Optional[str] = None,
        strategy_class_loader: Optional[Callable[[], type]] = None,
        template_row_migration: Optional[Callable[..., Any]] = None,
    ) -> None:
        """Initialize the instance.

        ``strategy_class_loader`` lets a provider defer importing its strategy
        (and the cloud SDK behind it) until ``strategy_class`` is first read.
        ``template_row_migration`` rewrites legacy template rows during
        template default resolution (see ``ProviderRegistryPort``).
        """
        super().__init__(
            type_name,
            strategy_factory,
            config_factory,
            resolver_factory=resolver_factory,
            validator_factory=validator_factory,
        )
        self.resolver_factory = resolver_factory
        self.validator_factory = validator_factory
        self._strategy_class = strategy_class
        self._strategy_class_loader = strategy_class_loader
        self.default_api = default_api
        self.display_name = display_name
        self.template_row_migration = template_row_migration

    @property
    def strategy_class(self) -> Optional[type]:
        """Return the strategy class, importing it on first access if deferred."""
        if self._strategy_class is None and self._strategy_class_loader is not None:
            self._strategy_class = self._strategy_class_loader()
            self._strategy_class_loader = None
        return self._strategy_class

    @strategy_class.setter
    def strategy_class(self, value: Optional[type]) -> None:
        self._strategy_class = value
