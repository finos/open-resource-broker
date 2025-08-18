"""
Domain DI Contracts - Interfaces and contracts for dependency injection.

This module defines the contracts and interfaces that infrastructure
implementations must fulfill, ensuring clear separation of concerns
while maintaining the power and flexibility of dependency injection.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar, Union

T = TypeVar("T")


class DIScope(Enum):
    """Dependency injection scopes."""

    SINGLETON = "singleton"
    TRANSIENT = "transient"
    SCOPED = "scoped"


class DILifecycle(Enum):
    """Dependency lifecycle management."""

    EAGER = "eager"
    LAZY = "lazy"


class DependencyRegistration:
    """
    Registration information for a dependency.

    This class encapsulates all information needed to register
    and resolve a dependency in the DI container.
    """

    def __init__(
        self,
        dependency_type: Type[T],
        implementation_type: Optional[Type[T]] = None,
        instance: Optional[T] = None,
        factory: Optional[Callable[[], T]] = None,
        scope: DIScope = DIScope.TRANSIENT,
        lifecycle: DILifecycle = DILifecycle.EAGER,
        dependencies: Optional[List[Type]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Initialize the instance."""
        self.dependency_type = dependency_type
        self.implementation_type = implementation_type or dependency_type
        self.instance = instance
        self.factory = factory
        self.scope = scope
        self.lifecycle = lifecycle
        self.dependencies = dependencies or []
        self.metadata = metadata or {}

    def is_singleton(self) -> bool:
        """Check if registration is for singleton scope."""
        return self.scope == DIScope.SINGLETON

    def is_lazy(self) -> bool:
        """Check if registration uses lazy lifecycle."""
        return self.lifecycle == DILifecycle.LAZY

    def has_factory(self) -> bool:
        """Check if registration has custom factory."""
        return self.factory is not None

    def has_instance(self) -> bool:
        """Check if registration has pre-created instance."""
        return self.instance is not None


class DIContainerPort(ABC):
    """
    Port for dependency injection container operations.

    This port defines the complete contract for DI container
    functionality, including registration, resolution, and lifecycle management.
    """

    @abstractmethod
    def register(self, registration: DependencyRegistration) -> None:
        """
        Register a dependency with full configuration.

        Args:
            registration: Complete registration information
        """

    @abstractmethod
    def register_type(
        self,
        dependency_type: Type[T],
        implementation_type: Optional[Type[T]] = None,
        scope: DIScope = DIScope.TRANSIENT,
    ) -> None:
        """
        Register a type with optional implementation.

        Args:
            dependency_type: The interface or base type
            implementation_type: The concrete implementation
            scope: The dependency scope
        """

    @abstractmethod
    def register_instance(self, dependency_type: Type[T], instance: T) -> None:
        """
        Register a pre-created instance.

        Args:
            dependency_type: The type to register
            instance: The instance to register
        """

    @abstractmethod
    def register_factory(
        self,
        dependency_type: Type[T],
        factory: Callable[[], T],
        scope: DIScope = DIScope.TRANSIENT,
    ) -> None:
        """
        Register a factory function.

        Args:
            dependency_type: The type to register
            factory: Factory function that creates instances
            scope: The dependency scope
        """

    @abstractmethod
    def register_singleton(
        self,
        dependency_type: Type[T],
        implementation_or_factory: Union[Type[T], Callable[[], T]],
    ) -> None:
        """
        Register a singleton dependency.

        Args:
            dependency_type: The type to register
            implementation_or_factory: Implementation type or factory function
        """

    @abstractmethod
    def get(self, dependency_type: Type[T]) -> T:
        """
        Resolve a dependency.

        Args:
            dependency_type: The type to resolve

        Returns:
            Instance of the requested type

        Raises:
            DependencyResolutionError: If dependency cannot be resolved
        """

    @abstractmethod
    def get_optional(self, dependency_type: Type[T]) -> Optional[T]:
        """
        Resolve an optional dependency.

        Args:
            dependency_type: The type to resolve

        Returns:
            Instance of the requested type or None if not registered
        """

    @abstractmethod
    def get_all(self, dependency_type: Type[T]) -> List[T]:
        """
        Resolve all instances of a type.

        Args:
            dependency_type: The type to resolve

        Returns:
            List of all registered instances of the type
        """

    @abstractmethod
    def is_registered(self, dependency_type: Type[T]) -> bool:
        """
        Check if a type is registered.

        Args:
            dependency_type: The type to check

        Returns:
            True if registered, False otherwise
        """

    @abstractmethod
    def unregister(self, dependency_type: Type[T]) -> bool:
        """
        Unregister a dependency.

        Args:
            dependency_type: The type to unregister

        Returns:
            True if unregistered, False if not found
        """

    @abstractmethod
    def clear(self) -> None:
        """Clear all registrations."""

    @abstractmethod
    def get_registrations(self) -> Dict[Type, DependencyRegistration]:
        """
        Get all current registrations.

        Returns:
            Dictionary mapping types to their registrations
        """


class DIServiceLocatorPort(ABC):
    """
    Port for service locator pattern.

    This provides a simplified interface for dependency resolution
    when full DI container functionality is not needed.
    """

    @abstractmethod
    def locate(self, service_type: Type[T]) -> T:
        """
        Locate a service by type.

        Args:
            service_type: The service type to locate

        Returns:
            Instance of the service
        """

    @abstractmethod
    def locate_optional(self, service_type: Type[T]) -> Optional[T]:
        """
        Locate an optional service by type.

        Args:
            service_type: The service type to locate

        Returns:
            Instance of the service or None if not found
        """


class DIConfigurationPort(ABC):
    """
    Port for DI container configuration.

    This port allows configuration of container behavior
    without coupling to specific implementations.
    """

    @abstractmethod
    def configure_auto_registration(self, enabled: bool) -> None:
        """
        Enable or disable automatic registration of @injectable classes.

        Args:
            enabled: Whether to enable auto-registration
        """

    @abstractmethod
    def configure_circular_dependency_detection(self, enabled: bool) -> None:
        """
        Enable or disable circular dependency detection.

        Args:
            enabled: Whether to enable detection
        """

    @abstractmethod
    def configure_lazy_loading(self, enabled: bool) -> None:
        """
        Enable or disable lazy loading of dependencies.

        Args:
            enabled: Whether to enable lazy loading
        """

    @abstractmethod
    def add_assembly_scan_path(self, path: str) -> None:
        """
        Add path to scan for @injectable classes.

        Args:
            path: Module path to scan
        """


class DIEventPort(ABC):
    """
    Port for DI container events.

    This port allows listening to container events for
    monitoring, logging, and debugging purposes.
    """

    @abstractmethod
    def on_dependency_registered(
        self, callback: Callable[[Type, DependencyRegistration], None]
    ) -> None:
        """
        Register callback for dependency registration events.

        Args:
            callback: Function to call when dependency is registered
        """

    @abstractmethod
    def on_dependency_resolved(self, callback: Callable[[Type, Any], None]) -> None:
        """
        Register callback for dependency resolution events.

        Args:
            callback: Function to call when dependency is resolved
        """

    @abstractmethod
    def on_dependency_creation_failed(self, callback: Callable[[Type, Exception], None]) -> None:
        """
        Register callback for dependency creation failures.

        Args:
            callback: Function to call when dependency creation fails
        """


class CompositeDIPort(DIContainerPort, DIServiceLocatorPort, DIConfigurationPort, DIEventPort):
    """
    Composite port combining all DI functionality.

    This port provides a complete interface for all DI operations,
    allowing infrastructure implementations to provide full functionality
    while maintaining clean separation of concerns.
    """


# CQRS-Specific Contracts


class CQRSHandlerRegistrationPort(ABC):
    """
    Port for CQRS handler registration.

    This port defines contracts for registering and resolving
    CQRS command, query, and event handlers.
    """

    @abstractmethod
    def register_command_handler(self, command_type: Type, handler_type: Type) -> None:
        """
        Register a command handler.

        Args:
            command_type: The command type
            handler_type: The handler implementation type
        """

    @abstractmethod
    def register_query_handler(self, query_type: Type, handler_type: Type) -> None:
        """
        Register a query handler.

        Args:
            query_type: The query type
            handler_type: The handler implementation type
        """

    @abstractmethod
    def register_event_handler(self, event_type: Type, handler_type: Type) -> None:
        """
        Register an event handler.

        Args:
            event_type: The event type
            handler_type: The handler implementation type
        """

    @abstractmethod
    def get_command_handler(self, command_type: Type) -> Any:
        """
        Get command handler for command type.

        Args:
            command_type: The command type

        Returns:
            Handler instance
        """

    @abstractmethod
    def get_query_handler(self, query_type: Type) -> Any:
        """
        Get query handler for query type.

        Args:
            query_type: The query type

        Returns:
            Handler instance
        """

    @abstractmethod
    def get_event_handlers(self, event_type: Type) -> List[Any]:
        """
        Get all event handlers for event type.

        Args:
            event_type: The event type

        Returns:
            List of handler instances
        """


# Exception Classes


class DependencyResolutionError(Exception):
    """Raised when dependency cannot be resolved."""

    def __init__(self, dependency_type: Type, message: str = None):
        """Initialize dependency resolution error."""
        self.dependency_type = dependency_type
        self.message = message or f"Cannot resolve dependency: {dependency_type}"
        super().__init__(self.message)


class CircularDependencyError(DependencyResolutionError):
    """Raised when circular dependency is detected."""

    def __init__(self, dependency_chain: List[Type]):
        """Initialize circular dependency error with dependency chain."""
        self.dependency_chain = dependency_chain
        chain_str = " -> ".join(str(t) for t in dependency_chain)
        message = f"Circular dependency detected: {chain_str}"
        super().__init__(dependency_chain[0], message)


class DependencyRegistrationError(Exception):
    """Raised when dependency registration fails."""

    def __init__(self, dependency_type: Type, message: str):
        self.dependency_type = dependency_type
        self.message = message
        super().__init__(f"Registration failed for {dependency_type}: {message}")


class DIConfigurationError(Exception):
    """Raised when DI container configuration is invalid."""
