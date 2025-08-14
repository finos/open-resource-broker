"""Storage service registrations for dependency injection."""

from src.domain.base.ports import ConfigurationPort, LoggingPort
from src.infrastructure.di.container import DIContainer
from src.infrastructure.logging.logger import get_logger
from src.infrastructure.persistence.factories.strategy_factory import (
    StorageStrategyFactory,
)


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
        storage_type = config.get_storage_strategy()  # Defaults to "json"
        
        logger = get_logger(__name__)
        
        # Register only the configured storage type
        if storage_type == "json":
            from src.infrastructure.persistence.json.registration import register_json_storage
            register_json_storage()
            logger.info(f"Registered configured storage strategy: {storage_type}")
        elif storage_type == "sql":
            from src.infrastructure.persistence.sql.registration import register_sql_storage
            register_sql_storage()
            logger.info(f"Registered configured storage strategy: {storage_type}")
        elif storage_type == "dynamodb":
            from src.providers.aws.persistence.dynamodb.registration import register_dynamodb_storage
            register_dynamodb_storage()
            logger.info(f"Registered configured storage strategy: {storage_type}")
        else:
            logger.warning(f"Unknown storage strategy: {storage_type}, falling back to json")
            from src.infrastructure.persistence.json.registration import register_json_storage
            register_json_storage()
            
    except Exception as e:
        logger = get_logger(__name__)
        logger.error(f"Failed to register configured storage strategy: {e}")
        # Fallback to json
        from src.infrastructure.persistence.json.registration import register_json_storage
        register_json_storage()
