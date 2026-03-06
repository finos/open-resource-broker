"""Scheduler Registry - Registry pattern for scheduler strategy factories."""

from typing import Any, Callable

from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.registry.base_registry import BaseRegistration, BaseRegistry, RegistryMode


class UnsupportedSchedulerError(Exception):
    """Exception raised when an unsupported scheduler type is requested."""


class SchedulerRegistration(BaseRegistration):
    """Scheduler registration container."""

    def __init__(
        self, scheduler_type: str, strategy_factory: Callable, config_factory: Callable
    ) -> None:
        """Initialize the instance."""
        super().__init__(scheduler_type, strategy_factory, config_factory)
        self.scheduler_type = scheduler_type


class SchedulerRegistry(BaseRegistry):
    """
    Registry for scheduler strategy factories.

    Uses SINGLE_CHOICE mode - only one scheduler strategy at a time.
    Thread-safe singleton implementation using integrated BaseRegistry.
    """

    def __init__(self) -> None:
        # Scheduler is SINGLE_CHOICE - only one scheduler strategy at a time
        super().__init__(mode=RegistryMode.SINGLE_CHOICE)

    def register(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **kwargs,
    ) -> None:
        """Register scheduler strategy factory - implements abstract method."""
        try:
            self.register_type(type_name, strategy_factory, config_factory, **kwargs)
        except ValueError as e:
            raise ConfigurationError(str(e))

    def create_strategy(self, type_name: str, config: Any) -> Any:
        """Create scheduler strategy - implements abstract method."""
        try:
            return self.create_strategy_by_type(type_name, config)
        except ValueError as e:
            raise UnsupportedSchedulerError(str(e))

    def ensure_type_registered(self, scheduler_type: str) -> None:
        """Ensure scheduler type is registered."""
        if not self.is_registered(scheduler_type):
            raise ValueError(f"Scheduler type '{scheduler_type}' not registered")

    def get_strategy_class(self, scheduler_type: str) -> type:
        """Get strategy class without instantiating it.

        Useful for calling classmethods before app initialization.
        """
        self.ensure_type_registered(scheduler_type)

        # Import the strategy class based on type
        if scheduler_type in ["hostfactory", "hf"]:
            from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
                HostFactorySchedulerStrategy,
            )

            return HostFactorySchedulerStrategy
        elif scheduler_type == "default":
            from orb.infrastructure.scheduler.default.default_strategy import (
                DefaultSchedulerStrategy,
            )

            return DefaultSchedulerStrategy
        else:
            raise ValueError(f"Unknown scheduler type: {scheduler_type}")

    def _create_registration(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **additional_factories,
    ) -> BaseRegistration:
        """Create scheduler-specific registration."""
        return SchedulerRegistration(type_name, strategy_factory, config_factory)


# Global singleton instance
_scheduler_registry_instance: SchedulerRegistry | None = None


def get_scheduler_registry() -> SchedulerRegistry:
    """Get the singleton scheduler registry instance."""
    global _scheduler_registry_instance
    if _scheduler_registry_instance is None:
        _scheduler_registry_instance = SchedulerRegistry()  # type: ignore[assignment]
    return _scheduler_registry_instance  # type: ignore[return-value]
