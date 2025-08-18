"""Scheduler Registry - Registry pattern for scheduler strategy factories."""

from typing import Any, Callable

from domain.base.exceptions import ConfigurationError

from .base_registry import BaseRegistration, BaseRegistry, RegistryMode


class UnsupportedSchedulerError(Exception):
    """Exception raised when an unsupported scheduler type is requested."""


class SchedulerRegistration(BaseRegistration):
    """Scheduler registration container."""

    def __init__(self, scheduler_type: str, strategy_factory: Callable, config_factory: Callable):
        """Initialize the instance."""
        super().__init__(scheduler_type, strategy_factory, config_factory)
        self.scheduler_type = scheduler_type


class SchedulerRegistry(BaseRegistry):
    """
    Registry for scheduler strategy factories.

    Uses SINGLE_CHOICE mode - only one scheduler strategy at a time.
    Thread-safe singleton implementation using integrated BaseRegistry.
    """

    def __init__(self):
        # Scheduler is SINGLE_CHOICE - only one scheduler strategy at a time
        super().__init__(mode=RegistryMode.SINGLE_CHOICE)

    def register(
        self,
        scheduler_type: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **kwargs,
    ):
        """Register scheduler strategy factory - implements abstract method."""
        try:
            self.register_type(scheduler_type, strategy_factory, config_factory, **kwargs)
        except ValueError as e:
            raise ConfigurationError(str(e) from e) from e

    def create_strategy(self, scheduler_type: str, config: Any) -> Any:
        """Create scheduler strategy - implements abstract method."""
        try:
            return self.create_strategy_by_type(scheduler_type, config)
        except ValueError as e:
            raise UnsupportedSchedulerError(str(e) from e) from e

    def ensure_type_registered(self, scheduler_type: str) -> None:
        """Ensure scheduler type is registered, register if not."""
        if not self.is_registered(scheduler_type):
            self._register_type_dynamically(scheduler_type)

    def _register_type_dynamically(self, scheduler_type: str) -> None:
        """Dynamically register scheduler type based on configuration."""
        try:
            if scheduler_type in ["hostfactory", "hf"]:
                from infrastructure.scheduler.registration import (
                    register_symphony_hostfactory_scheduler,
                )

                register_symphony_hostfactory_scheduler()
            elif scheduler_type == "default":
                from infrastructure.scheduler.registration import (
                    register_default_scheduler,
                )

                register_default_scheduler()
            else:
                raise ValueError(f"Unknown scheduler type: {scheduler_type}") from e
        except ImportError as e:
            raise ConfigurationError(f"Scheduler type '{scheduler_type}' not available: {e}") from e

    def _create_registration(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **additional_factories,
    ) -> BaseRegistration:
        """Create scheduler-specific registration."""
        return SchedulerRegistration(type_name, strategy_factory, config_factory)


def get_scheduler_registry() -> SchedulerRegistry:
    """Get the singleton scheduler registry instance."""
    return SchedulerRegistry()
