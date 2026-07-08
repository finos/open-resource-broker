"""Authentication strategy registry."""

from typing import Any, Callable

from orb.infrastructure.adapters.ports.auth import AuthPort
from orb.infrastructure.registry.base_registry import BaseRegistry, RegistryMode


class AuthRegistry(BaseRegistry):
    """Registry for authentication strategies."""

    def __init__(self) -> None:
        """Initialize authentication registry."""
        super().__init__(mode=RegistryMode.SINGLE_CHOICE)

    def register(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **kwargs,
    ) -> None:
        """Register a strategy factory."""
        self.register_type(type_name, strategy_factory, config_factory, **kwargs)

    def create_strategy(self, type_name: str, config: Any) -> Any:
        """Create a strategy instance."""
        return self.create_strategy_by_type(type_name, config)

    def register_strategy(
        self, strategy_name: str, strategy_factory: Callable[..., AuthPort]
    ) -> None:
        """
        Register an authentication strategy.

        Args:
            strategy_name: Name of the strategy (e.g., 'none', 'bearer_token', 'oauth')
            strategy_factory: Factory callable that creates the strategy instance.
                Must expose a ``from_auth_config(auth_config)`` classmethod.
        """

        # Create a simple config factory that passes through kwargs
        def config_factory(**kwargs):
            return kwargs

        self.register_type(strategy_name, strategy_factory, config_factory)

    def get_strategy(self, strategy_name: str, auth_config: Any) -> AuthPort:
        """
        Get an authentication strategy instance built from *auth_config*.

        Internally delegates to ``strategy_factory.from_auth_config(auth_config)``
        so that each strategy class owns its own config-extraction logic.

        Args:
            strategy_name: Name of the registered strategy
            auth_config: AuthConfig instance passed to ``from_auth_config``

        Returns:
            Authentication strategy instance

        Raises:
            ValueError: If strategy is not registered
        """
        registration = self._get_type_registration(strategy_name)
        return registration.strategy_factory.from_auth_config(auth_config)  # type: ignore[union-attr]

    def list_strategies(self) -> list[str]:
        """
        List all registered authentication strategies.

        Returns:
            List of strategy names
        """
        return self.get_registered_types()


# Global registry instance using BaseRegistry singleton pattern
def get_auth_registry() -> AuthRegistry:
    """
    Get the global authentication registry instance.

    Returns:
        Global authentication registry
    """
    registry = AuthRegistry()
    # Ensure default strategies are registered
    _register_default_strategies(registry)  # type: ignore[arg-type]
    return registry  # type: ignore[return-value]


def _register_default_strategies(registry: AuthRegistry) -> None:  # type: ignore[misc]
    """Register default authentication strategies."""
    # Only register if not already registered (idempotent)
    if not registry.get_registered_types():
        # Register no-auth strategy
        from .strategy.no_auth_strategy import NoAuthStrategy

        registry.register_strategy("none", NoAuthStrategy)

        # Register bearer token strategy
        from .strategy.bearer_token_strategy import BearerTokenStrategy

        registry.register_strategy("bearer_token", BearerTokenStrategy)

        # Register enhanced bearer token strategy
        from .strategy.bearer_token_strategy_enhanced import EnhancedBearerTokenStrategy

        registry.register_strategy("bearer_token_enhanced", EnhancedBearerTokenStrategy)
