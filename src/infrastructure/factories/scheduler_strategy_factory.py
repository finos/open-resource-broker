"""Scheduler strategy factory using scheduler registry pattern.

This factory creates scheduler strategies using the scheduler registry pattern,
eliminating hard-coded scheduler conditionals and maintaining clean architecture.
"""

from typing import Any, Dict, Optional

from infrastructure.logging.logger import get_logger
from infrastructure.registry.scheduler_registry import get_scheduler_registry


class SchedulerStrategyFactory:
    """Factory for creating scheduler strategy components using scheduler registry."""

    def __init__(self, config_manager: Optional[Any] = None) -> None:
        """Initialize factory with optional configuration manager."""
        self.logger = get_logger(__name__)
        self.config_manager = config_manager
        self._scheduler_registry = None
        self._strategy_cache: Dict[str, Any] = {}

    @property
    def scheduler_registry(self):
        """Lazy load scheduler registry."""
        if self._scheduler_registry is None:
            self._scheduler_registry = get_scheduler_registry()
        return self._scheduler_registry

    def create_strategy(self, scheduler_type: str, config: Any) -> Any:
        """
        Create scheduler strategy using scheduler registry.

        Args:
            scheduler_type: Type of scheduler ('hostfactory', 'default')
            config: Configuration for the scheduler strategy

        Returns:
            Scheduler strategy instance
        """
        cache_key = f"{scheduler_type}_{hash(str(config))}"

        if cache_key not in self._strategy_cache:
            try:
                strategy = self.scheduler_registry.create_strategy(scheduler_type, config)
                self._strategy_cache[cache_key] = strategy
                self.logger.debug("Created %s scheduler strategy", scheduler_type)
            except Exception as e:
                self.logger.error("Failed to create %s scheduler strategy: %s", scheduler_type, e)
                raise

        return self._strategy_cache[cache_key]
