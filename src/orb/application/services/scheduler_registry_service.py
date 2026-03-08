"""Application service interface for scheduler registry operations."""

from typing import Any

from orb.domain.base.ports.logging_port import LoggingPort


class SchedulerRegistryService:
    """Application service interface for scheduler registry operations."""

    def __init__(self, registry: Any, logger: LoggingPort):
        self._registry = registry
        self._logger = logger

    def get_available_schedulers(self) -> list[str]:
        """Get list of available scheduler types."""
        return self._registry.get_registered_types()

    def create_scheduler_strategy(self, scheduler_type: str, config: Any) -> Any:
        """Create scheduler strategy instance."""
        return self._registry.create_strategy(scheduler_type, config)

    def is_scheduler_registered(self, scheduler_type: str) -> bool:
        """Check if scheduler type is registered."""
        return self._registry.is_registered(scheduler_type)

    def get_scheduler_capabilities(self, scheduler_type: str) -> dict[str, Any]:
        """Get scheduler capabilities (if supported)."""
        try:
            strategy = self._registry.create_strategy(scheduler_type, {})
            return getattr(strategy, "get_capabilities", lambda: {})()
        except Exception:
            return {}
