"""
DI contracts — interfaces, registrations, and lifecycle enums.

Relocated from orb.domain.base.di_contracts.  These are infrastructure-wiring
contracts; they have no domain meaning.

Exception classes (DependencyResolutionError, CircularDependencyError, etc.)
live in :mod:`orb.infrastructure.di.exceptions`.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

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

    Encapsulates all information needed to register and resolve a dependency
    in the DI container.
    """

    def __init__(
        self,
        dependency_type: type[T],
        implementation_type: Optional[type[T]] = None,
        instance: Optional[T] = None,
        factory: Optional[Callable[[], T]] = None,
        scope: DIScope = DIScope.TRANSIENT,
        lifecycle: DILifecycle = DILifecycle.EAGER,
        dependencies: Optional[list[type]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
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

    Defines the complete contract for DI container functionality including
    registration, resolution, and lifecycle management.
    """

    @abstractmethod
    def register(self, registration: DependencyRegistration) -> None:
        """Register a dependency with full configuration."""

    @abstractmethod
    def register_type(
        self,
        dependency_type: type[T],
        implementation_type: Optional[type[T]] = None,
        scope: DIScope = DIScope.TRANSIENT,
    ) -> None:
        """Register a type with optional implementation."""

    @abstractmethod
    def register_instance(self, dependency_type: type[T], instance: T) -> None:
        """Register a pre-created instance."""

    @abstractmethod
    def register_factory(
        self,
        dependency_type: type[T],
        factory: Callable[[], T],
        scope: DIScope = DIScope.TRANSIENT,
    ) -> None:
        """Register a factory function."""

    @abstractmethod
    def register_singleton(
        self,
        dependency_type: type[T],
        implementation_or_factory: type[T] | Callable[[], T],
    ) -> None:
        """Register a singleton dependency."""

    @abstractmethod
    def get(self, dependency_type: type[T]) -> T:
        """Resolve a dependency."""

    @abstractmethod
    def get_optional(self, dependency_type: type[T]) -> Optional[T]:
        """Resolve an optional dependency (returns None if not registered)."""

    @abstractmethod
    def get_all(self, dependency_type: type[T]) -> list[T]:
        """Resolve all instances of a type."""

    @abstractmethod
    def is_registered(self, dependency_type: type[T]) -> bool:
        """Check if a type is registered."""

    @abstractmethod
    def unregister(self, dependency_type: type[T]) -> bool:
        """Unregister a dependency.  Returns True if removed, False if not found."""

    @abstractmethod
    def clear(self) -> None:
        """Clear all registrations."""

    @abstractmethod
    def get_registrations(self) -> dict[type, DependencyRegistration]:
        """Get all current registrations."""


class DIServiceLocatorPort(ABC):
    """Port for service locator pattern (simplified resolution interface)."""

    @abstractmethod
    def locate(self, service_type: type[T]) -> T:
        """Locate a service by type."""

    @abstractmethod
    def locate_optional(self, service_type: type[T]) -> Optional[T]:
        """Locate an optional service by type (returns None if not found)."""


class DIConfigurationPort(ABC):
    """Port for DI container configuration."""

    @abstractmethod
    def configure_auto_registration(self, enabled: bool) -> None:
        """Enable or disable automatic registration of @injectable classes."""

    @abstractmethod
    def configure_circular_dependency_detection(self, enabled: bool) -> None:
        """Enable or disable circular dependency detection."""

    @abstractmethod
    def configure_lazy_loading(self, enabled: bool) -> None:
        """Enable or disable lazy loading of dependencies."""

    @abstractmethod
    def add_assembly_scan_path(self, path: str) -> None:
        """Add path to scan for @injectable classes."""


class DIEventPort(ABC):
    """Port for DI container events (monitoring, logging, debugging)."""

    @abstractmethod
    def on_dependency_registered(
        self, callback: Callable[[type, DependencyRegistration], None]
    ) -> None:
        """Register callback for dependency registration events."""

    @abstractmethod
    def on_dependency_resolved(self, callback: Callable[[type, Any], None]) -> None:
        """Register callback for dependency resolution events."""

    @abstractmethod
    def on_dependency_creation_failed(self, callback: Callable[[type, Exception], None]) -> None:
        """Register callback for dependency creation failures."""


class CompositeDIPort(DIContainerPort, DIServiceLocatorPort, DIConfigurationPort, DIEventPort):
    """Composite port combining all DI functionality."""


# CQRS-Specific Contracts


class CQRSHandlerRegistrationPort(ABC):
    """Port for CQRS handler registration and resolution."""

    @abstractmethod
    def register_command_handler(self, command_type: type, handler_type: type) -> None:
        """Register a command handler."""

    @abstractmethod
    def register_query_handler(self, query_type: type, handler_type: type) -> None:
        """Register a query handler."""

    @abstractmethod
    def register_event_handler(self, event_type: type, handler_type: type) -> None:
        """Register an event handler."""

    @abstractmethod
    def get_command_handler(self, command_type: type) -> Any:
        """Get command handler for command type."""

    @abstractmethod
    def get_query_handler(self, query_type: type) -> Any:
        """Get query handler for query type."""

    @abstractmethod
    def get_event_handlers(self, event_type: type) -> list[Any]:
        """Get all event handlers for event type."""


# Alias for legacy name used in di_contracts.py — exception already in exceptions.py
# but some callers import DependencyInjectionPort from here; provide a stub.
class DependencyInjectionPort(ABC):
    """
    Simple port for DI container operations (legacy alias).

    Prefer DIContainerPort for new code.
    """

    @abstractmethod
    def get(self, cls: type[T]) -> T:
        """Resolve dependency by type."""

    @abstractmethod
    def register(self, cls: type[T], instance_or_factory: T | Callable[[], T]) -> None:
        """Register dependency in container."""

    @abstractmethod
    def register_singleton(self, cls: type[T], instance_or_factory: T | Callable[[], T]) -> None:
        """Register dependency as singleton."""

    @abstractmethod
    def is_registered(self, cls: type[T]) -> bool:
        """Check if type is registered in container."""
