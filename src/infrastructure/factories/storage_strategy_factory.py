"""Storage strategy factory using storage registry pattern.

This factory creates storage strategies using the storage registry pattern,
eliminating hard-coded storage conditionals and maintaining clean architecture.
"""

from typing import Any, Dict, Optional

from infrastructure.logging.logger import get_logger
from infrastructure.persistence.base.strategy import BaseStorageStrategy
from infrastructure.registry.storage_registry import get_storage_registry


class StorageStrategyFactory:
    """Factory for creating storage strategy components using storage registry."""

    def __init__(self, config_manager: Optional[Any] = None):
        """Initialize factory with optional configuration manager."""
        self.logger = get_logger(__name__)
        self.config_manager = config_manager
        self._storage_registry = None
        self._strategy_cache: Dict[str, BaseStorageStrategy] = {}

    @property
    def storage_registry(self):
        """Lazy load storage registry."""
        if self._storage_registry is None:
            self._storage_registry = get_storage_registry()
        return self._storage_registry

    def create_strategy(self, storage_type: str, config: Any) -> BaseStorageStrategy:
        """
        Create storage strategy using storage registry.

        Args:
            storage_type: Type of storage ('json', 'sql', 'dynamodb')
            config: Configuration for the storage strategy

        Returns:
            Storage strategy instance
        """
        cache_key = f"{storage_type}_{hash(str(config))}"

        if cache_key not in self._strategy_cache:
            try:
                strategy = self.storage_registry.create_strategy(storage_type, config)
                self._strategy_cache[cache_key] = strategy
                self.logger.debug(f"Created {storage_type} storage strategy")
            except Exception as e:
                self.logger.error(f"Failed to create {storage_type} storage strategy: {e}")
                raise

        return self._strategy_cache[cache_key]

    def create_machine_storage_strategy(self, config: Optional[Any] = None) -> BaseStorageStrategy:
        """Create storage strategy for machine entities."""
        if config is None and self.config_manager:
            config = self.config_manager.get_app_config()

        storage_type = self._get_storage_type(config)
        return self.create_strategy(storage_type, config)

    def create_request_storage_strategy(self, config: Optional[Any] = None) -> BaseStorageStrategy:
        """Create storage strategy for request entities."""
        if config is None and self.config_manager:
            config = self.config_manager.get_app_config()

        storage_type = self._get_storage_type(config)
        return self.create_strategy(storage_type, config)

    def create_template_storage_strategy(self, config: Optional[Any] = None) -> BaseStorageStrategy:
        """Create storage strategy for template entities."""
        if config is None and self.config_manager:
            config = self.config_manager.get_app_config()

        storage_type = self._get_storage_type(config)
        return self.create_strategy(storage_type, config)

    def _get_storage_type(self, config: Any) -> str:
        """Extract storage type from configuration."""
        if hasattr(config, "storage") and hasattr(config.storage, "strategy"):
            return config.storage.strategy
        elif self.config_manager:
            return self.config_manager.get_storage_strategy()
        else:
            return "json"  # Default fallback

    def clear_cache(self) -> None:
        """Clear strategy cache."""
        self._strategy_cache.clear()
        self.logger.debug("Storage strategy cache cleared")
