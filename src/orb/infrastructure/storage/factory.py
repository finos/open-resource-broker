"""Storage strategy factory using orchestrator delegation pattern.

This factory delegates to entity-specific factories via an orchestrator,
following SRP while maintaining backward compatibility.
"""

from typing import Any, Optional

from orb.infrastructure.storage.base.strategy import BaseStorageStrategy

from .factories.storage_factory_orchestrator import StorageFactoryOrchestrator


class StorageStrategyFactory:
    """Legacy factory that delegates to orchestrator for backward compatibility."""

    def __init__(self, config_manager: Optional[Any] = None) -> None:
        """Initialize factory with orchestrator delegation."""
        self._orchestrator = StorageFactoryOrchestrator(config_manager)

    def create_strategy(self, storage_type: str, config: Any) -> BaseStorageStrategy:
        """Create storage strategy (delegates to orchestrator)."""
        return self._orchestrator.create_strategy(storage_type, config)

    def create_machine_storage_strategy(self, config: Optional[Any] = None) -> BaseStorageStrategy:
        """Create machine storage strategy (delegates to orchestrator)."""
        if config is None and self._orchestrator.config_manager:
            config = self._orchestrator.config_manager.app_config.model_dump()

        storage_type = self._orchestrator._get_storage_type(config)
        return self._orchestrator.create_machine_storage_strategy(storage_type, config)

    def create_request_storage_strategy(self, config: Optional[Any] = None) -> BaseStorageStrategy:
        """Create request storage strategy (delegates to orchestrator)."""
        if config is None and self._orchestrator.config_manager:
            config = self._orchestrator.config_manager.app_config.model_dump()

        storage_type = self._orchestrator._get_storage_type(config)
        return self._orchestrator.create_request_storage_strategy(storage_type, config)

    def create_template_storage_strategy(self, config: Optional[Any] = None) -> BaseStorageStrategy:
        """Create template storage strategy (delegates to orchestrator)."""
        if config is None and self._orchestrator.config_manager:
            config = self._orchestrator.config_manager.app_config.model_dump()

        storage_type = self._orchestrator._get_storage_type(config)
        return self._orchestrator.create_template_storage_strategy(storage_type, config)

    def clear_cache(self) -> None:
        """Clear strategy cache (delegates to orchestrator)."""
        self._orchestrator.clear_cache()

    def _get_storage_type(self, config: Any) -> str:
        """Get storage type from configuration (delegates to orchestrator)."""
        return self._orchestrator._get_storage_type(config)
