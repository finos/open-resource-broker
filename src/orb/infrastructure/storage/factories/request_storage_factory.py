"""Request storage factory for creating request-specific storage strategies."""

from typing import Any

from orb.infrastructure.storage.base.strategy import BaseStorageStrategy


class RequestStorageFactory:
    """Factory for creating request-specific storage strategies."""

    def __init__(self, config_manager: Any = None):
        self.config_manager = config_manager
        self._strategy_cache: dict[str, BaseStorageStrategy] = {}

    def create_strategy(self, storage_type: str, config: Any) -> BaseStorageStrategy:
        """Create request storage strategy."""
        cache_key = f"request_{storage_type}_{id(config)}"

        if cache_key not in self._strategy_cache:
            strategy = self._create_strategy_instance(storage_type, config, "requests")
            self._strategy_cache[cache_key] = strategy

        return self._strategy_cache[cache_key]

    def _create_strategy_instance(
        self, storage_type: str, config: Any, entity_type: str
    ) -> BaseStorageStrategy:
        """Create strategy instance for specific storage type."""
        from orb.infrastructure.storage.registry import get_storage_registry

        registry = get_storage_registry()
        return registry.create_strategy(storage_type, config)  # type: ignore[arg-type]

    def clear_cache(self) -> None:
        """Clear strategy cache."""
        self._strategy_cache.clear()
