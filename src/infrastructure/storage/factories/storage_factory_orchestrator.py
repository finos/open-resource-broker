"""Storage factory orchestrator for coordinating entity-specific storage factories."""

from typing import Any

from infrastructure.storage.base.strategy import BaseStorageStrategy

from .machine_storage_factory import MachineStorageFactory
from .request_storage_factory import RequestStorageFactory
from .template_storage_factory import TemplateStorageFactory


class StorageFactoryOrchestrator:
    """Orchestrates entity-specific storage factories."""

    def __init__(self, config_manager: Any = None):
        self.config_manager = config_manager
        self._machine_factory = MachineStorageFactory(config_manager)
        self._request_factory = RequestStorageFactory(config_manager)
        self._template_factory = TemplateStorageFactory(config_manager)

    def create_machine_storage_strategy(
        self, storage_type: str, config: Any
    ) -> BaseStorageStrategy:
        """Create machine storage strategy."""
        return self._machine_factory.create_strategy(storage_type, config)

    def create_request_storage_strategy(
        self, storage_type: str, config: Any
    ) -> BaseStorageStrategy:
        """Create request storage strategy."""
        return self._request_factory.create_strategy(storage_type, config)

    def create_template_storage_strategy(
        self, storage_type: str, config: Any
    ) -> BaseStorageStrategy:
        """Create template storage strategy."""
        return self._template_factory.create_strategy(storage_type, config)

    def create_strategy(
        self, storage_type: str, config: Any, entity_type: str = "entities"
    ) -> BaseStorageStrategy:
        """Create storage strategy for specified entity type."""
        if entity_type == "machines":
            return self.create_machine_storage_strategy(storage_type, config)
        elif entity_type == "requests":
            return self.create_request_storage_strategy(storage_type, config)
        elif entity_type == "templates":
            return self.create_template_storage_strategy(storage_type, config)
        else:
            # Default to machine factory for backward compatibility
            return self.create_machine_storage_strategy(storage_type, config)

    def clear_cache(self) -> None:
        """Clear all factory caches."""
        self._machine_factory.clear_cache()
        self._request_factory.clear_cache()
        self._template_factory.clear_cache()

    def _get_storage_type(self, config: Any) -> str:
        """Get storage type from configuration."""
        if hasattr(config, "storage") and hasattr(config.storage, "strategy"):
            return config.storage.strategy
        elif self.config_manager:
            return self.config_manager.get_storage_strategy()
        return "json"  # Default fallback
