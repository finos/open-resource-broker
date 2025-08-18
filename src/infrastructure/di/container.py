"""
Dependency Injection Container Implementation
"""

import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Set, Type, TypeVar

from domain.base.dependency_injection import is_injectable
from domain.base.di_contracts import (
    CQRSHandlerRegistrationPort,
    DependencyRegistration,
    DIContainerPort,
    DIScope,
)
from domain.base.ports import ContainerPort
from infrastructure.di.components import (
    CQRSHandlerRegistry,
    DependencyResolver,
    ServiceRegistry,
)
from infrastructure.di.exceptions import (
    CircularDependencyError,
    DependencyResolutionError,
    UnregisteredDependencyError,
)
from infrastructure.logging.logger import get_logger

T = TypeVar("T")
logger = get_logger(__name__)


class LazyLoadingConfig:
    """Configuration for lazy loading behavior."""

    def __init__(self, config_dict: Optional[Dict[str, Any]] = None):
        """Initialize lazy loading configuration with provided settings."""
        if config_dict is None:
            config_dict = {}

        self.enabled = config_dict.get("enabled", True)
        self.cache_instances = config_dict.get("cache_instances", True)
        self.discovery_mode = config_dict.get("discovery_mode", "lazy")
        self.connection_mode = config_dict.get("connection_mode", "lazy")
        self.preload_critical = config_dict.get("preload_critical", [])

    @classmethod
    def from_config_manager(cls, config_manager=None) -> "LazyLoadingConfig":
        """Create lazy loading config from configuration manager."""
        if config_manager is None:
            try:
                from config.manager import get_config_manager

                config_manager = get_config_manager()
            except ImportError:
                return cls()

        try:
            performance_config = config_manager.get("performance", {})
            lazy_config = performance_config.get("lazy_loading", {})
            return cls(lazy_config)
        except Exception:
            return cls()


@contextmanager
def timed_operation(operation_name: str) -> Iterator[None]:
    """Context manager for timing DI operations."""
    start_time = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - start_time
        if elapsed > 0.1:
            logger.warning("Slow DI operation '%s': %ss", operation_name, elapsed:.3f)
        else:
            logger.debug("DI operation '%s': %ss", operation_name, elapsed:.3f)


class DIContainer(DIContainerPort, CQRSHandlerRegistrationPort, ContainerPort):
    """
    Dependency injection container using composition of focused components.
    Includes lazy loading capabilities for improved startup performance.
    """

    def __init__(self):
        """Initialize the DI container."""
        self._service_registry = ServiceRegistry()
        self._cqrs_registry = CQRSHandlerRegistry()
        self._dependency_resolver = DependencyResolver(self._service_registry, self._cqrs_registry)
        self._lock = threading.RLock()

        # Lazy loading support
        self._lazy_config = LazyLoadingConfig.from_config_manager()
        self._lazy_factories: Dict[Type, Any] = {}
        self._on_demand_registrations: Dict[Type, Any] = {}

        logger.info(
            "DI Container initialized (lazy_loading=%s)",  'enabled' if self._lazy_config.enabled else 'disabled'
        )

    def is_registered(self, cls: Type) -> bool:
        """Check if a class is registered."""
        return self._service_registry.is_registered(cls)

    def has(self, service_type: Type[T]) -> bool:
        """Check if service type is registered."""
        return self._service_registry.has(service_type)

    def register_singleton(self, cls: Type[T], instance_or_factory: Any = None) -> None:
        """Register a singleton service."""
        with timed_operation(f"register_singleton({cls.__name__})"):
            self._service_registry.register_singleton(cls, instance_or_factory)

    def register_factory(self, cls: Type[T], factory) -> None:
        """Register a factory for creating instances."""
        with timed_operation(f"register_factory({cls.__name__})"):
            self._service_registry.register_factory(cls, factory)

    def register_instance(self, cls: Type[T], instance: T) -> None:
        """Register a specific instance."""
        with timed_operation(f"register_instance({cls.__name__})"):
            self._service_registry.register_instance(cls, instance)

    def register(self, registration: DependencyRegistration) -> None:
        """Register a dependency registration."""
        self._service_registry.register(registration)

    def register_type(
        self,
        interface_type: Type[T],
        implementation_type: Type[T],
        registration_type=None,
    ) -> None:
        """Register an interface to implementation mapping."""
        scope = registration_type or DIScope.TRANSIENT
        self._service_registry.register_type(interface_type, implementation_type, scope)

    def unregister(self, dependency_type: Type[T]) -> bool:
        """Unregister a dependency type."""
        return self._service_registry.unregister(dependency_type)

    def get_registrations(self) -> Dict[Type, DependencyRegistration]:
        """Get all registrations."""
        return self._service_registry.get_registrations()

    def get(
        self,
        cls: Type[T],
        parent_type: Optional[Type] = None,
        dependency_chain: Optional[Set[Type]] = None,
    ) -> T:
        """Get an instance of the specified type with lazy loading support."""
        try:
            # First, try normal resolution
            return self._dependency_resolver.resolve(cls, parent_type, dependency_chain)
        except (DependencyResolutionError, UnregisteredDependencyError):
            # If lazy loading is enabled, try on-demand registration
            if self._lazy_config.enabled and not self._service_registry.is_registered(cls):
                self._register_on_demand(cls)

                # Try resolution again after on-demand registration
                if self._service_registry.is_registered(cls):
                    return self._dependency_resolver.resolve(cls, parent_type, dependency_chain)

            # Re-raise the original exception if lazy loading didn't help
            raise
        except Exception as e:
            if isinstance(e, CircularDependencyError):
                raise
            else:
                raise DependencyResolutionError(cls, f"Failed to resolve {cls.__name__}: {str(e)}")

    def get_optional(self, dependency_type: Type[T]) -> Optional[T]:
        """Get an optional instance of the specified type."""
        try:
            return self.get(dependency_type)
        except (DependencyResolutionError, UnregisteredDependencyError):
            return None

    def get_all(self, dependency_type: Type[T]) -> List[T]:
        """Get all instances of the specified type."""
        instance = self.get_optional(dependency_type)
        return [instance] if instance is not None else []

    def register_command_handler(self, command_type: Type, handler_type: Type) -> None:
        """Register a command handler."""
        self._cqrs_registry.register_command_handler(command_type, handler_type)
        if not self._service_registry.is_registered(handler_type):
            self._service_registry.register_type(handler_type, handler_type)

    def register_query_handler(self, query_type: Type, handler_type: Type) -> None:
        """Register a query handler."""
        self._cqrs_registry.register_query_handler(query_type, handler_type)
        if not self._service_registry.is_registered(handler_type):
            self._service_registry.register_type(handler_type, handler_type)

    def register_event_handler(self, event_type: Type, handler_type: Type) -> None:
        """Register an event handler."""
        self._cqrs_registry.register_event_handler(event_type, handler_type)
        if not self._service_registry.is_registered(handler_type):
            self._service_registry.register_type(handler_type, handler_type)

    def get_command_handler(self, command_type: Type) -> Any:
        """Get command handler for a command type."""
        handler_type = self._cqrs_registry.get_command_handler_type(command_type)
        if handler_type is None:
            raise DependencyResolutionError(
                command_type,
                f"No command handler registered for {command_type.__name__}",
            )
        return self.get(handler_type)

    def get_query_handler(self, query_type: Type) -> Any:
        """Get query handler for a query type."""
        handler_type = self._cqrs_registry.get_query_handler_type(query_type)
        if handler_type is None:
            raise DependencyResolutionError(
                query_type, f"No query handler registered for {query_type.__name__}"
            )
        return self.get(handler_type)

    def get_event_handlers(self, event_type: Type) -> List[Any]:
        """Get event handlers for an event type."""
        handler_types = self._cqrs_registry.get_event_handler_types(event_type)
        return [self.get(handler_type) for handler_type in handler_types]

    def register_injectable_class(self, cls: Type[T]) -> None:
        """Register a class as injectable."""
        self._service_registry.register_injectable_class(cls)

        if hasattr(cls, "_command_type"):
            self.register_command_handler(cls._command_type, cls)
        if hasattr(cls, "_query_type"):
            self.register_query_handler(cls._query_type, cls)
        if hasattr(cls, "_event_type"):
            self.register_event_handler(cls._event_type, cls)

    # ========== LAZY LOADING METHODS ==========

    def register_lazy_factory(self, cls: Type[T], factory) -> None:
        """Register a lazy factory that creates instances on first access."""
        if self._lazy_config.enabled:
            with self._lock:
                self._lazy_factories[cls] = factory
                logger.debug("Registered lazy factory for %s", cls.__name__)
        else:
            # Fallback to immediate registration
            self.register_factory(cls, factory)

    def register_on_demand(self, cls: Type[T], registration_func) -> None:
        """Register a function that will register the service on first access."""
        if self._lazy_config.enabled:
            with self._lock:
                self._on_demand_registrations[cls] = registration_func
                logger.debug("Registered on-demand registration for %s", cls.__name__)
        else:
            # Immediate registration
            registration_func(self)

    def _register_on_demand(self, cls: Type[T]) -> None:
        """Register a service on-demand if lazy registration is available."""
        if not self._lazy_config.enabled:
            return

        with self._lock:
            # Check if we have a lazy factory
            if cls in self._lazy_factories:
                factory = self._lazy_factories.pop(cls)
                self.register_factory(cls, factory)
                logger.debug("Lazy factory registered for %s", cls.__name__)
                return

            # Check if we have an on-demand registration
            if cls in self._on_demand_registrations:
                registration_func = self._on_demand_registrations.pop(cls)
                registration_func(self)
                logger.debug("On-demand registration completed for %s", cls.__name__)
                return

            # Try to auto-register injectable classes
            if is_injectable(cls):
                self.register_injectable_class(cls)
                logger.debug("Auto-registered injectable class %s", cls.__name__)
                return

    def _create_and_cache(self, cls: Type[T]) -> T:
        """Create an instance and cache it if caching is enabled."""
        # This method is called by the get() method
        # For now, delegate to the dependency resolver
        return self._dependency_resolver.resolve(cls)

    def is_lazy_loading_enabled(self) -> bool:
        """Check if lazy loading is enabled."""
        return self._lazy_config.enabled

    def get_lazy_config(self) -> LazyLoadingConfig:
        """Get the lazy loading configuration."""
        return self._lazy_config

    def clear(self) -> None:
        """Clear all registrations."""
        with self._lock:
            self._service_registry.clear()
            self._cqrs_registry.clear()
            self._dependency_resolver.clear_cache()
            logger.info("DI Container cleared")

    def get_stats(self) -> Dict[str, Any]:
        """Get container statistics."""
        with self._lock:
            return {
                "service_registry": self._service_registry.get_stats(),
                "cqrs_registry": self._cqrs_registry.get_stats(),
                "container_type": "modular",
            }


# Singleton container management
_container_instance: Optional[DIContainer] = None
_container_lock = threading.Lock()


def get_container() -> DIContainer:
    """Get the singleton DI container instance."""
    global _container_instance

    with _container_lock:
        if _container_instance is None:
            _container_instance = _create_configured_container()
        return _container_instance


def _create_configured_container() -> DIContainer:
    """Create and configure the DI container."""
    container = DIContainer()

    logger.info("DI Container configured and ready")
    return container


def _setup_cqrs_infrastructure(container: DIContainer) -> None:
    """Set up CQRS infrastructure: handler discovery and buses."""
    try:
        from domain.base.ports import LoggingPort
        from infrastructure.di.buses import BusFactory
        from infrastructure.di.handler_discovery import create_handler_discovery_service

        logger.info("Setting up CQRS infrastructure")

        # Ensure infrastructure services are registered first (for lazy loading)
        if container.is_lazy_loading_enabled():
            logger.info("Ensuring infrastructure services are available for CQRS setup")
            _ensure_infrastructure_services(container)

        # Discover and register all handlers
        logger.info("Creating handler discovery service")
        discovery_service = create_handler_discovery_service(container)

        logger.info("Starting handler discovery")
        discovery_service.discover_and_register_handlers()

        # Check registration results
        try:
            from application.decorators import get_handler_registry_stats

            stats = get_handler_registry_stats()
            logger.info("Handler discovery results: %s", stats)
        except ImportError:
            logger.debug("Handler registry stats not available")

        # Create and register buses
        logger.info("Creating CQRS buses")
        logging_port = container.get(LoggingPort)
        query_bus, command_bus = BusFactory.create_buses(container, logging_port)

        # Register buses as singletons
        from infrastructure.di.buses import CommandBus, QueryBus

        container.register_instance(QueryBus, query_bus)
        container.register_instance(CommandBus, command_bus)

        logger.info("CQRS infrastructure setup complete")

    except ImportError as e:
        # Fallback if CQRS infrastructure is not available
        logger.debug("CQRS infrastructure not available: %s", e)
    except Exception as e:
        logger.warning("Failed to setup CQRS infrastructure: %s", e)


def _ensure_infrastructure_services(container: DIContainer) -> None:
    """Ensure infrastructure services are registered for CQRS setup."""
    try:
        from infrastructure.di.infrastructure_services import (
            register_infrastructure_services,
        )

        logger.debug("Registering infrastructure services for CQRS setup")
        register_infrastructure_services(container)
    except Exception as e:
        logger.warning("Failed to ensure infrastructure services: %s", e)


def reset_container() -> None:
    """Reset the global container instance."""
    global _container_instance
    with _container_lock:
        if _container_instance:
            _container_instance.clear()
        _container_instance = None


__all__ = [
    "DIContainer",
    "get_container",
    "reset_container",
    "timed_operation",
    "_setup_cqrs_infrastructure",
]
