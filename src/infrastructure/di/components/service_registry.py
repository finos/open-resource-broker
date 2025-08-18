"""Service registration management for DI container."""

import logging
import threading
from typing import Any, Callable, Dict, Optional, Set, Type, TypeVar

from domain.base.di_contracts import DependencyRegistration, DIScope

T = TypeVar("T")
logger = logging.getLogger(__name__)


class ServiceRegistry:
    """Manages service registration for dependency injection."""

    def __init__(self):
        """Initialize the instance."""
        self._registrations: Dict[Type, DependencyRegistration] = {}
        self._singletons: Dict[Type, Any] = {}
        self._lock = threading.RLock()
        self._injectable_classes: Set[Type] = set()

    def is_registered(self, cls: Type) -> bool:
        """Check if a class is registered."""
        with self._lock:
            return cls in self._registrations

    def has(self, service_type: Type[T]) -> bool:
        """Check if service type is registered."""
        with self._lock:
            return service_type in self._registrations

    def register_singleton(self, cls: Type[T], instance_or_factory: Any = None) -> None:
        """Register a singleton service."""
        with self._lock:
            if instance_or_factory is None:
                # Register class itself as singleton
                registration = DependencyRegistration(
                    dependency_type=cls,
                    scope=DIScope.SINGLETON,
                    implementation_type=cls,
                )
            elif callable(instance_or_factory) and not isinstance(instance_or_factory, type):
                # Register factory function
                registration = DependencyRegistration(
                    dependency_type=cls,
                    scope=DIScope.SINGLETON,
                    factory=instance_or_factory,
                )
            else:
                # Register instance
                registration = DependencyRegistration(
                    dependency_type=cls,
                    scope=DIScope.SINGLETON,
                    instance=instance_or_factory,
                )
                self._singletons[cls] = instance_or_factory

            self._registrations[cls] = registration
            logger.debug("Registered singleton: %s", cls.__name__)

    def register_factory(self, cls: Type[T], factory: Callable[..., T]) -> None:
        """Register a factory for creating instances."""
        with self._lock:
            registration = DependencyRegistration(
                dependency_type=cls, scope=DIScope.TRANSIENT, factory=factory
            )
            self._registrations[cls] = registration
            logger.debug("Registered factory for: %s", cls.__name__)

    def register_instance(self, cls: Type[T], instance: T) -> None:
        """Register a specific instance."""
        with self._lock:
            registration = DependencyRegistration(
                dependency_type=cls, scope=DIScope.SINGLETON, instance=instance
            )
            self._registrations[cls] = registration
            self._singletons[cls] = instance
            logger.debug("Registered instance: %s", cls.__name__)

    def register(self, registration: DependencyRegistration) -> None:
        """Register a dependency registration."""
        with self._lock:
            self._registrations[registration.dependency_type] = registration

            # If it's an instance registration, store the instance
            if registration.scope == DIScope.SINGLETON and registration.instance:
                self._singletons[registration.dependency_type] = registration.instance

            logger.debug("Registered: %s", registration.dependency_type.__name__)

    def register_type(
        self,
        interface_type: Type[T],
        implementation_type: Type[T],
        scope: DIScope = DIScope.TRANSIENT,
    ) -> None:
        """Register an interface to implementation mapping."""
        with self._lock:
            registration = DependencyRegistration(
                dependency_type=interface_type,
                scope=scope,
                implementation_type=implementation_type,
            )
            self._registrations[interface_type] = registration
            logger.debug(
                "Registered type mapping: %s -> %s", interface_type.__name__, implementation_type.__name__
            )

    def register_injectable_class(self, cls: Type[T]) -> None:
        """Register a class as injectable (auto-registration)."""
        with self._lock:
            self._injectable_classes.add(cls)

            # Auto-register if not already registered
            if not self.is_registered(cls):
                registration = DependencyRegistration(
                    dependency_type=cls,
                    scope=DIScope.TRANSIENT,
                    implementation_type=cls,
                )
                self._registrations[cls] = registration

            logger.debug("Registered injectable class: %s", cls.__name__)

    def get_registration(self, dependency_type: Type[T]) -> Optional[DependencyRegistration]:
        """Get registration for a dependency type."""
        with self._lock:
            return self._registrations.get(dependency_type)

    def get_registrations(self) -> Dict[Type, DependencyRegistration]:
        """Get all registrations."""
        with self._lock:
            return self._registrations.copy()

    def get_singleton_instance(self, dependency_type: Type[T]) -> Optional[T]:
        """Get cached singleton instance."""
        with self._lock:
            return self._singletons.get(dependency_type)

    def set_singleton_instance(self, dependency_type: Type[T], instance: T) -> None:
        """Cache singleton instance."""
        with self._lock:
            self._singletons[dependency_type] = instance

    def unregister(self, dependency_type: Type[T]) -> bool:
        """Unregister a dependency type."""
        with self._lock:
            if dependency_type in self._registrations:
                del self._registrations[dependency_type]
                # Also remove singleton instance if exists
                if dependency_type in self._singletons:
                    del self._singletons[dependency_type]
                logger.debug("Unregistered: %s", dependency_type.__name__)
                return True
            return False

    def clear(self) -> None:
        """Clear all registrations."""
        with self._lock:
            self._registrations.clear()
            self._singletons.clear()
            self._injectable_classes.clear()
            logger.info("Service registry cleared")

    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        with self._lock:
            return {
                "total_registrations": len(self._registrations),
                "singleton_instances": len(self._singletons),
                "injectable_classes": len(self._injectable_classes),
                "scope_types": {
                    scope.value: sum(
                        1 for reg in self._registrations.values() if reg.scope == scope
                    )
                    for scope in DIScope
                },
            }
