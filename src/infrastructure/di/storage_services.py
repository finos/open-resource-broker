"""Storage service registrations for dependency injection."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infrastructure.di.container import DIContainer
    from infrastructure.storage.factory import StorageStrategyFactory


def register_storage_services(container: "DIContainer") -> None:
    """Register storage services respecting lazy loading configuration."""
    
    # Lazy imports to avoid import cascade
    from domain.base.ports import ConfigurationPort
    from infrastructure.storage.factory import StorageStrategyFactory
    from infrastructure.logging.logger import get_logger

    # Register storage strategy factory only
    container.register_factory(StorageStrategyFactory, create_storage_strategy_factory)

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


def create_storage_strategy_factory(container: "DIContainer") -> "StorageStrategyFactory":
    """Create storage strategy factory with configuration."""
    config = container.get(ConfigurationPort)
    return StorageStrategyFactory(config_manager=config)


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
            logger.warning("Failed to preload critical storage type %s: %s", storage_type, e)


def _register_configured_storage_strategy(container: "DIContainer") -> None:
    """Register only the configured storage strategy."""
    try:
        config = container.get(ConfigurationPort)
        storage_type = config.get_storage_strategy()

        logger = get_logger(__name__)

        # Registry handles dynamic registration - no hardcoded types here
        from infrastructure.storage.registry import get_storage_registry

        registry = get_storage_registry()
        registry.ensure_type_registered(storage_type)

        logger.info("Registered configured storage strategy: %s", storage_type)

    except Exception as e:
        logger = get_logger(__name__)
        logger.error("Failed to register configured storage strategy: %s", e)
        # Fallback to json
        from infrastructure.storage.registry import get_storage_registry

        registry = get_storage_registry()
        registry.ensure_type_registered("json")
        logger.info("Registered fallback storage strategy: json")
