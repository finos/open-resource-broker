"""Integrated base registry supporting both single-choice and multi-choice patterns."""

import threading
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Optional


class RegistryMode(Enum):
    """Registry operation modes."""

    SINGLE_CHOICE = "single_choice"  # Storage/Scheduler: one strategy at a time
    MULTI_CHOICE = "multi_choice"  # Provider: multiple strategies simultaneously


class BaseRegistration(ABC):
    """Base registration container with extensible factory support."""

    def __init__(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **additional_factories,
    ) -> None:
        """
        Initialize base registration.

        Args:
            type_name: Type identifier
            strategy_factory: Factory for creating strategies
            config_factory: Factory for creating configurations
            **additional_factories: Additional factories (resolver_factory, validator_factory, etc.)
        """
        self.type_name = type_name
        self.strategy_factory = strategy_factory
        self.config_factory = config_factory
        self.additional_factories = additional_factories

    def get_factory(self, factory_name: str) -> Optional[Callable]:
        """Get additional factory by name."""
        return self.additional_factories.get(factory_name)


class BaseRegistry(ABC):
    """Integrated base registry supporting both single-choice and multi-choice patterns."""

    _instances: dict[str, "BaseRegistry"] = {}
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Ensure singleton instance per registry type."""
        registry_name = cls.__name__
        if registry_name not in cls._instances:
            with cls._lock:
                if registry_name not in cls._instances:
                    cls._instances[registry_name] = super().__new__(cls)
        return cls._instances[registry_name]

    def __init__(self, mode: RegistryMode = RegistryMode.SINGLE_CHOICE, factory=None) -> None:
        """
        Initialize registry with specified mode and factory.

        Args:
            mode: Registry operation mode (single or multi choice)
            factory: RegistryFactory for dependency injection
        """
        if hasattr(self, "_initialized"):
            return

        self.mode = mode
        self._factory = factory
        # Type-based registrations
        self._type_registrations: dict[str, BaseRegistration] = {}
        self._instance_registrations: dict[
            str, BaseRegistration
        ] = {}  # Instance-based registrations (multi-choice only)
        self._registry_lock = threading.RLock()  # Use RLock for nested locking

        self._initialized = True

    @abstractmethod
    def register(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **kwargs,
    ) -> None:
        """Register a strategy factory."""

    @abstractmethod
    def create_strategy(self, type_name: str, config: Any) -> Any:
        """Create a strategy instance."""

    def register_type(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **additional_factories,
    ) -> None:
        """
        Register a type with its factories (idempotent operation).

        Args:
            type_name: Type identifier
            strategy_factory: Factory for creating strategies
            config_factory: Factory for creating configurations
            **additional_factories: Additional factories (resolver_factory, validator_factory, etc.)
        """
        with self._registry_lock:
            if type_name in self._type_registrations:
                # Idempotent operation - log debug and return
                return

            registration = self._create_registration(
                type_name, strategy_factory, config_factory, **additional_factories
            )
            self._type_registrations[type_name] = registration

            # Register with factory if available
            if self._factory:
                self._factory.register_constructor(type_name, strategy_factory)

    def register_instance(
        self,
        type_name: str,
        instance_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **additional_factories,
    ) -> None:
        """
        Register a named instance (multi-choice mode only).

        Args:
            type_name: Type identifier
            instance_name: Unique instance name
            strategy_factory: Factory for creating strategies
            config_factory: Factory for creating configurations
            **additional_factories: Additional factories

        Raises:
            ValueError: If not in multi-choice mode or instance already registered
        """
        if self.mode != RegistryMode.MULTI_CHOICE:
            raise ValueError("Instance registration only supported in MULTI_CHOICE mode")

        with self._registry_lock:
            if instance_name in self._instance_registrations:
                # Idempotent operation - log debug and return
                return

            registration = self._create_registration(
                type_name, strategy_factory, config_factory, **additional_factories
            )
            self._instance_registrations[instance_name] = registration

            # Register with factory if available
            if self._factory:
                self._factory.register_constructor(instance_name, strategy_factory)

    def create_strategy_by_type(self, type_name: str, config: Any) -> Any:
        """Create strategy by type name."""
        registration = self._get_type_registration(type_name)

        # Use factory if available, otherwise use registration directly
        if self._factory:
            # For factory, we need to call the constructor with config as first argument
            return registration.strategy_factory(config)
        else:
            return self._create_strategy_from_registration(registration, config, type_name)

    def create_strategy_by_instance(self, instance_name: str, config: Any) -> Any:
        """Create strategy by instance name (multi-choice mode only)."""
        if self.mode != RegistryMode.MULTI_CHOICE:
            raise ValueError("Instance-based creation only supported in MULTI_CHOICE mode")

        registration = self._get_instance_registration(instance_name)

        # Use factory if available, otherwise use registration directly
        if self._factory:
            # For factory, we need to call the constructor with config as first argument
            return registration.strategy_factory(config)
        else:
            return self._create_strategy_from_registration(registration, config, instance_name)

    def is_registered(self, type_name: str) -> bool:
        """Check if a type is registered."""
        with self._registry_lock:
            return type_name in self._type_registrations

    def is_instance_registered(self, instance_name: str) -> bool:
        """Check if an instance is registered."""
        with self._registry_lock:
            return instance_name in self._instance_registrations

    def get_registered_types(self) -> list[str]:
        """Get list of registered types."""
        with self._registry_lock:
            return list(self._type_registrations.keys())

    def get_registered_instances(self) -> list[str]:
        """Get list of registered instances."""
        with self._registry_lock:
            return list(self._instance_registrations.keys())

    def format_not_registered_error(self, requested_item: str, registry_type: str) -> str:
        """Format standardized error message for unregistered items.

        Args:
            requested_item: The item that was requested
            registry_type: Type of registry (provider, scheduler, storage)

        Returns:
            Formatted error message with available options
        """
        available_types = self.get_registered_types()
        available_instances = self.get_registered_instances()

        if not available_types and not available_instances:
            return f"No {registry_type}s registered"

        parts = [f"{registry_type.title()} '{requested_item}' not found"]

        if available_types:
            parts.append(f"Available {registry_type} types: {', '.join(available_types)}")

        if available_instances:
            parts.append(f"Available {registry_type} instances: {', '.join(available_instances)}")

        return ". ".join(parts)

    def format_registry_error(self, requested_item: str, registry_type: str) -> str:
        """Format error message for unregistered registry item.

        Args:
            requested_item: The item that was requested
            registry_type: Type of registry (provider, scheduler, etc.)

        Returns:
            Formatted error message with available options

        Note:
            Backward compatibility alias for format_not_registered_error
        """
        return self.format_not_registered_error(requested_item, registry_type)

    def unregister_type(self, type_name: str) -> bool:
        """Unregister a type."""
        with self._registry_lock:
            if type_name in self._type_registrations:
                del self._type_registrations[type_name]
                return True
            return False

    def unregister_instance(self, instance_name: str) -> bool:
        """Unregister an instance."""
        with self._registry_lock:
            if instance_name in self._instance_registrations:
                del self._instance_registrations[instance_name]
                return True
            return False

    def create_additional_component(
        self, type_name: str, factory_name: str, config: Any = None
    ) -> Optional[Any]:
        """Create additional component (resolver, validator, etc.) by type."""
        registration = self._get_type_registration(type_name)
        factory = registration.get_factory(factory_name)
        if factory is None:
            return None

        try:
            if config is not None:
                component = factory(config)
            else:
                component = factory()
            return component
        except Exception:
            return None

    def ensure_types_registered(self, register_function: Callable) -> None:
        """Ensure types are registered (idempotent operation)."""
        if not self.get_registered_types():
            register_function()

    def get_available_types_with_registration(self, register_function: Callable) -> list[str]:
        """Get available types, ensuring registration first."""
        self.ensure_types_registered(register_function)
        return self.get_registered_types()

    def clear_registrations(self) -> None:
        """Clear all registrations (primarily for testing)."""
        with self._registry_lock:
            self._type_registrations.clear()
            self._instance_registrations.clear()

    # Protected methods for subclass implementation

    def _create_registration(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **additional_factories,
    ) -> BaseRegistration:
        """Create registration object - can be overridden by subclasses."""
        return BaseRegistration(type_name, strategy_factory, config_factory, **additional_factories)

    def _get_type_registration(self, type_name: str) -> BaseRegistration:
        """Get type registration with error handling."""
        with self._registry_lock:
            if type_name not in self._type_registrations:
                available_types = ", ".join(self.get_registered_types())
                raise ValueError(
                    f"Type '{type_name}' is not registered. Available types: {available_types}"
                )
            return self._type_registrations[type_name]

    def _get_instance_registration(self, instance_name: str) -> BaseRegistration:
        """Get instance registration with error handling."""
        with self._registry_lock:
            if instance_name not in self._instance_registrations:
                available_instances = ", ".join(self.get_registered_instances())
                raise ValueError(
                    f"Instance '{instance_name}' is not registered. Available instances: {available_instances}"
                )
            return self._instance_registrations[instance_name]

    def _create_strategy_from_registration(
        self, registration: BaseRegistration, config: Any, identifier: str
    ) -> Any:
        """Create strategy from registration with error handling."""
        try:
            strategy = registration.strategy_factory(config)
            return strategy
        except Exception as e:
            from domain.base.exceptions import ConfigurationError

            error_msg = f"Failed to create strategy for '{identifier}': {e!s}"
            raise ConfigurationError(error_msg)
