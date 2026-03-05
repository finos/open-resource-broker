"""Storage service registrations for dependency injection."""

from typing import TYPE_CHECKING

from domain.base.ports import ConfigurationPort
from infrastructure.logging.logger import get_logger
from infrastructure.storage.factory import StorageStrategyFactory

if TYPE_CHECKING:
    from infrastructure.di.container import DIContainer


def register_storage_services(container: "DIContainer") -> None:
    """Register storage services respecting lazy loading configuration."""

    # Register new orchestrator with lazy initialization
    def create_orchestrator(c):
        from infrastructure.storage.factories.storage_factory_orchestrator import (
            StorageFactoryOrchestrator,
        )

        config = c.get(ConfigurationPort)
        return StorageFactoryOrchestrator(config_manager=config)

    from infrastructure.storage.factories.storage_factory_orchestrator import (
        StorageFactoryOrchestrator,
    )

    container.register_factory(StorageFactoryOrchestrator, create_orchestrator)

    # Register storage strategy factory with lazy initialization
    def create_factory(c):
        config = c.get(ConfigurationPort)
        return StorageStrategyFactory(config_manager=config)

    container.register_factory(StorageStrategyFactory, create_factory)

    # Register repository components
    _register_repository_components(container)

    # ALWAYS register JSON storage as it's the default and most critical
    from infrastructure.storage.registry import get_storage_registry

    registry = get_storage_registry()
    registry.ensure_type_registered("json")

    # Respect lazy loading configuration for other types
    lazy_config = container.get_lazy_config()

    if lazy_config.discovery_mode == "eager":
        # Eager mode: register configured storage immediately
        _register_configured_storage_strategy(container)
    elif lazy_config.preload_critical:
        # Preload critical storage types only
        _register_critical_storage_types(container, lazy_config.preload_critical)
    # Lazy mode (default): JSON already registered above, other types will register on-demand


def _register_repository_components(container: "DIContainer") -> None:
    """Register repository components for dependency injection."""
    from infrastructure.storage.components import (
        InMemoryEventPublisher,
        LoggingEventPublisher,
        MemoryEntityCache,
        MemoryVersionManager,
        NoOpEntityCache,
        NoOpEventPublisher,
        NoOpVersionManager,
    )

    # Register default implementations
    container.register_singleton(MemoryEntityCache)
    container.register_singleton(MemoryVersionManager)
    container.register_singleton(LoggingEventPublisher)

    # Register no-op implementations for testing/minimal setups
    container.register_singleton(NoOpEntityCache)
    container.register_singleton(NoOpVersionManager)
    container.register_singleton(NoOpEventPublisher)
    container.register_singleton(InMemoryEventPublisher)


def _register_critical_storage_types(container: "DIContainer", critical_types: list[str]) -> None:
    """Register critical storage types specified in preload_critical."""
    logger = get_logger(__name__)

    from infrastructure.storage.registry import get_storage_registry

    registry = get_storage_registry()

    for storage_type in critical_types:
        try:
            registry.ensure_type_registered(storage_type)
            logger.info("Preloaded critical storage type: %s", storage_type)
        except Exception as e:
            logger.warning(
                "Failed to preload critical storage type %s: %s", storage_type, e, exc_info=True
            )


def _register_configured_storage_strategy(container: "DIContainer") -> None:
    """Register only the configured storage strategy."""
    # Make this lazy too - don't access config during registration
    # For now, just register JSON as the default
    from infrastructure.storage.registry import get_storage_registry

    registry = get_storage_registry()
    registry.ensure_type_registered("json")

    logger = get_logger(__name__)
    logger.info("Registered fallback storage strategy: json")
