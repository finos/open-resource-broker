"""Scheduler Registry - Registry pattern for scheduler strategy factories."""

from typing import Any, Callable, ClassVar

from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.registry.base_registry import BaseRegistration, BaseRegistry, RegistryMode


class UnsupportedSchedulerError(Exception):
    """Exception raised when an unsupported scheduler type is requested."""


class SchedulerRegistration(BaseRegistration):
    """Scheduler registration container."""

    def __init__(
        self,
        scheduler_type: str,
        strategy_factory: Callable,
        config_factory: Callable,
        strategy_class: type | None = None,
    ) -> None:
        """Initialize the instance."""
        super().__init__(scheduler_type, strategy_factory, config_factory)
        self.scheduler_type = scheduler_type
        self.strategy_class = strategy_class


class SchedulerRegistry(BaseRegistry):
    """
    Registry for scheduler strategy factories.

    Uses SINGLE_CHOICE mode - only one scheduler strategy at a time.
    Thread-safe singleton implementation using integrated BaseRegistry.
    """

    _SCHEDULER_METADATA: ClassVar[dict[str, dict[str, str]]] = {
        "default": {"display_name": "default", "description": "Standalone usage"},
        "hostfactory": {
            "display_name": "hostfactory",
            "description": "IBM Spectrum Symphony integration",
        },
        "hf": {
            "display_name": "hostfactory",
            "description": "IBM Spectrum Symphony integration",
        },
    }

    _SCHEDULER_EXTRA_CONFIG: ClassVar[dict[str, dict[str, str]]] = {
        "hostfactory": {"config_root": "$ORB_CONFIG_DIR"},
        "hf": {"config_root": "$ORB_CONFIG_DIR"},
    }

    def __init__(self) -> None:
        # Scheduler is SINGLE_CHOICE - only one scheduler strategy at a time
        super().__init__(mode=RegistryMode.SINGLE_CHOICE)

    def get_display_metadata(self, scheduler_type: str) -> dict[str, str]:
        """Return display_name and description for a registered scheduler type.

        Falls back to type name as display_name if not in metadata map.
        """
        return self._SCHEDULER_METADATA.get(
            scheduler_type,
            {"display_name": scheduler_type, "description": f"{scheduler_type} scheduler"},
        )

    def get_extra_config_for_type(self, scheduler_type: str) -> dict[str, str]:
        """Return extra config keys to inject under scheduler config for this type.

        Returns empty dict for types with no extra config (e.g. 'default').
        """
        return self._SCHEDULER_EXTRA_CONFIG.get(scheduler_type, {})

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
        registration = self._get_type_registration(scheduler_type)
        assert isinstance(registration, SchedulerRegistration)
        if registration.strategy_class is None:
            raise ValueError(f"No strategy class registered for scheduler type: {scheduler_type}")
        return registration.strategy_class

    def _create_registration(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **additional_factories,
    ) -> BaseRegistration:
        """Create scheduler-specific registration."""
        strategy_class = additional_factories.pop("strategy_class", None)
        return SchedulerRegistration(type_name, strategy_factory, config_factory, strategy_class=strategy_class)


# Global singleton instance
_scheduler_registry_instance: SchedulerRegistry | None = None


def get_scheduler_registry() -> SchedulerRegistry:
    """Get the singleton scheduler registry instance."""
    global _scheduler_registry_instance
    if _scheduler_registry_instance is None:
        _scheduler_registry_instance = SchedulerRegistry()  # type: ignore[assignment]
    return _scheduler_registry_instance  # type: ignore[return-value]
