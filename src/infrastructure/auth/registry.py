"""Authentication strategy registry."""

import threading
from typing import Callable, Dict

from src.infrastructure.logging.logger import get_logger
from src.infrastructure.ports.auth import AuthPort


class AuthRegistry:
    """Registry for authentication strategies."""

    def __init__(self):
        """Initialize authentication registry."""
        self._strategies: Dict[str, Callable[..., AuthPort]] = {}
        self._lock = threading.Lock()
        self.logger = get_logger(__name__)

    def register_strategy(
        self, strategy_name: str, strategy_factory: Callable[..., AuthPort]
    ) -> None:
        """
        Register an authentication strategy.

        Args:
            strategy_name: Name of the strategy (e.g., 'none', 'bearer_token', 'oauth')
            strategy_factory: Factory function that creates the strategy instance
        """
        with self._lock:
            if strategy_name in self._strategies:
                self.logger.warning(f"Overriding existing auth strategy: {strategy_name}")

            self._strategies[strategy_name] = strategy_factory
            self.logger.info(f"Registered auth strategy: {strategy_name}")

    def get_strategy(self, strategy_name: str, **kwargs) -> AuthPort:
        """
        Get an authentication strategy instance.

        Args:
            strategy_name: Name of the strategy
            **kwargs: Arguments to pass to the strategy factory

        Returns:
            Authentication strategy instance

        Raises:
            ValueError: If strategy is not registered
        """
        with self._lock:
            if strategy_name not in self._strategies:
                available = list(self._strategies.keys())
                raise ValueError(
                    f"Auth strategy '{strategy_name}' not registered. "
                    f"Available strategies: {available}"
                )

            strategy_factory = self._strategies[strategy_name]
            return strategy_factory(**kwargs)

    def list_strategies(self) -> list[str]:
        """
        List all registered authentication strategies.

        Returns:
            List of strategy names
        """
        with self._lock:
            return list(self._strategies.keys())

    def is_registered(self, strategy_name: str) -> bool:
        """
        Check if a strategy is registered.

        Args:
            strategy_name: Name of the strategy

        Returns:
            True if strategy is registered
        """
        with self._lock:
            return strategy_name in self._strategies


# Global registry instance
_auth_registry: AuthRegistry = None
_registry_lock = threading.Lock()


def get_auth_registry() -> AuthRegistry:
    """
    Get the global authentication registry instance.

    Returns:
        Global authentication registry
    """
    global _auth_registry

    if _auth_registry is None:
        with _registry_lock:
            if _auth_registry is None:
                _auth_registry = AuthRegistry()
                _register_default_strategies()

    return _auth_registry


def _register_default_strategies() -> None:
    """Register default authentication strategies."""
    registry = _auth_registry

    # Register no-auth strategy
    from .strategies.no_auth_strategy import NoAuthStrategy

    registry.register_strategy("none", NoAuthStrategy)

    # Register bearer token strategy
    from .strategies.bearer_token_strategy import BearerTokenStrategy

    registry.register_strategy("bearer_token", BearerTokenStrategy)
