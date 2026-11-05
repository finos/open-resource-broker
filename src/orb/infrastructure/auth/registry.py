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


class LazyAuthStrategy:
    """Strategy factory that imports its class on first use.

    Strategy modules pull in JWT, boto3 and similar libraries. Registering them
    lazily keeps those imports off the startup path of commands that never
    authenticate a request.
    """

    def __init__(self, module_path: str, class_name: str) -> None:
        self._module_path = module_path
        self._class_name = class_name

    def resolve(self) -> Any:
        """Return the real strategy class, importing it if necessary."""
        import importlib

        return getattr(importlib.import_module(self._module_path), self._class_name)

    def from_auth_config(self, auth_config: Any) -> Any:
        """Build the strategy from *auth_config* via the real class."""
        return self.resolve().from_auth_config(auth_config)


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
        registry.register_strategy(
            "bearer_token",
            LazyAuthStrategy(
                "orb.infrastructure.auth.strategy.bearer_token_strategy", "BearerTokenStrategy"
            ),
        )

        # Register enhanced bearer token strategy
        registry.register_strategy(
            "bearer_token_enhanced",
            LazyAuthStrategy(
                "orb.infrastructure.auth.strategy.bearer_token_strategy_enhanced",
                "EnhancedBearerTokenStrategy",
            ),
        )
