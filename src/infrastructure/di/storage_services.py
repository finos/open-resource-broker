"""Storage service registrations for dependency injection."""

from src.domain.base.ports import ConfigurationPort
from src.infrastructure.di.container import DIContainer
from src.infrastructure.factories.storage_strategy_factory import StorageStrategyFactory
from src.infrastructure.logging.logger import get_logger


def register_storage_services(container: DIContainer) -> None:
    """Register storage services with configuration-driven strategy loading."""

    # Register storage strategy factory
    container.register_factory(StorageStrategyFactory, create_storage_strategy_factory)

    # Register only the configured storage strategy
    _register_configured_storage_strategy(container)


def create_storage_strategy_factory(container: DIContainer) -> StorageStrategyFactory:
    """Create storage strategy factory with configuration."""
    config = container.get(ConfigurationPort)
    return StorageStrategyFactory(config_manager=config)


def _register_configured_storage_strategy(container: DIContainer) -> None:
    """Register only the configured storage strategy."""
    try:
        config = container.get(ConfigurationPort)
        storage_type = config.get_storage_strategy()

        logger = get_logger(__name__)

        # Registry handles dynamic registration - no hardcoded types here
        from src.infrastructure.registry.storage_registry import get_storage_registry

        registry = get_storage_registry()
        registry.ensure_type_registered(storage_type)

        logger.info(f"Registered configured storage strategy: {storage_type}")

    except Exception as e:
        logger = get_logger(__name__)
        logger.error(f"Failed to register configured storage strategy: {e}")
        # Fallback to json
        from src.infrastructure.registry.storage_registry import get_storage_registry

        registry = get_storage_registry()
        registry.ensure_type_registered("json")
        logger.info("Registered fallback storage strategy: json")
