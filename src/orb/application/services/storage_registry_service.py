from typing import Any

from orb.domain.base.ports.logging_port import LoggingPort


class StorageRegistryService:
    """Application service interface for storage registry operations."""

    def __init__(self, registry: Any, logger: LoggingPort):
        self._registry = registry
        self._logger = logger

    def get_available_storage_types(self) -> list[str]:
        """Get list of available storage types."""
        return self._registry.get_registered_types()

    def create_storage_strategy(self, storage_type: str, config: Any) -> Any:
        """Create storage strategy instance."""
        return self._registry.create_strategy(storage_type, config)

    def is_storage_registered(self, storage_type: str) -> bool:
        """Check if storage type is registered."""
        return self._registry.is_registered(storage_type)

    def get_storage_health(self, storage_type: str) -> dict[str, Any]:
        """Get storage health status."""
        try:
            strategy = self._registry.create_strategy(storage_type, {})
            return getattr(strategy, "check_health", lambda: {"status": "unknown"})()
        except Exception as e:
            return {"status": "error", "message": str(e)}
