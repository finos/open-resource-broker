"""Storage strategy factory delegating directly to the storage registry.

The factory is a thin convenience wrapper that resolves the storage type from
configuration and forwards the call to the storage registry.  All repository
creation on the production hot-path goes through RepositoryFactory →
StorageRegistry directly; this class exists for callers that hold a
StorageStrategyFactory reference and call its named convenience methods.
"""

from typing import Any, Optional

from orb.infrastructure.storage.base.strategy import BaseStorageStrategy
from orb.infrastructure.storage.registry import get_storage_registry


class StorageStrategyFactory:
    """Thin factory that delegates strategy creation to the storage registry."""

    def __init__(self, config_manager: Optional[Any] = None) -> None:
        """Initialize factory with optional configuration manager."""
        self.config_manager = config_manager

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_storage_type(self, config: Any) -> str:
        """Derive storage type from config dict/object or fall back to config_manager."""
        if hasattr(config, "storage") and hasattr(config.storage, "strategy"):
            return config.storage.strategy
        if isinstance(config, dict):
            storage = config.get("storage") or {}
            if isinstance(storage, dict) and storage.get("strategy"):
                return storage["strategy"]
        if self.config_manager:
            return self.config_manager.get_storage_strategy()
        return "json"

    # ------------------------------------------------------------------
    # Public API — keep signatures stable so existing callers don't break
    # ------------------------------------------------------------------

    def create_strategy(self, storage_type: str, config: Any) -> BaseStorageStrategy:
        """Create a storage strategy for the given type and configuration."""
        return get_storage_registry().create_strategy(storage_type, config)  # type: ignore[return-value]

    def create_machine_storage_strategy(self, config: Optional[Any] = None) -> BaseStorageStrategy:
        """Create a machine storage strategy."""
        if config is None and self.config_manager:
            config = self.config_manager.app_config.model_dump()
        storage_type = self._get_storage_type(config)
        return get_storage_registry().create_strategy(storage_type, config, entity="machines")  # type: ignore[return-value]

    def create_request_storage_strategy(self, config: Optional[Any] = None) -> BaseStorageStrategy:
        """Create a request storage strategy."""
        if config is None and self.config_manager:
            config = self.config_manager.app_config.model_dump()
        storage_type = self._get_storage_type(config)
        return get_storage_registry().create_strategy(storage_type, config, entity="requests")  # type: ignore[return-value]

    def create_template_storage_strategy(self, config: Optional[Any] = None) -> BaseStorageStrategy:
        """Create a template storage strategy."""
        if config is None and self.config_manager:
            config = self.config_manager.app_config.model_dump()
        storage_type = self._get_storage_type(config)
        return get_storage_registry().create_strategy(storage_type, config, entity="templates")  # type: ignore[return-value]

    def clear_cache(self) -> None:
        """No-op: caching is handled by the registry; kept for API compatibility."""
