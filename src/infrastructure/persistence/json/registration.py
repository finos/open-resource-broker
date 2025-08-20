"""JSON Storage Registration Module.

This module provides registration functions for JSON storage type,
enabling the storage registry pattern for JSON persistence.

CLEAN ARCHITECTURE: Only handles storage strategies, no repository knowledge.
"""

from typing import Any, Dict

from infrastructure.logging.logger import get_logger
from infrastructure.registry.storage_registry import get_storage_registry


def create_json_strategy(config: Any) -> Any:
    """
    Create JSON storage strategy from configuration.

    Args:
        config: Configuration object containing JSON storage settings

    Returns:
        JSONStorageStrategy instance
    """
    from infrastructure.persistence.json.strategy import JSONStorageStrategy

    # Extract configuration parameters
    if hasattr(config, "json_strategy"):
        json_config = config.json_strategy
        base_path = json_config.base_path
        storage_type = json_config.storage_type

        if storage_type == "single_file":
            file_path = f"{base_path}/{json_config.filenames['single_file']}"
        else:
            # For split files, we'll use a base path and let the strategy handle file
            # naming
            file_path = base_path
    else:
        # Use configured file path or fallback to default
        file_path = getattr(config, "file_path", "data/request_database.json")

    return JSONStorageStrategy(file_path=file_path, create_dirs=True, entity_type="generic")


def create_json_config(data: Dict[str, Any]) -> Any:
    """
    Create JSON storage configuration from data.

    Args:
        data: Configuration data dictionary

    Returns:
        JSON configuration object
    """
    from config.schemas.storage_schema import JsonStrategyConfig

    return JsonStrategyConfig(**data)


def create_json_unit_of_work(config: Any) -> Any:
    """
    Create JSON unit of work with correct configuration extraction.

    Args:
        config: Configuration object (ConfigurationManager or dict)

    Returns:
        JSONUnitOfWork instance with correctly extracted configuration
    """
    from config.manager import ConfigurationManager
    from config.schemas.storage_schema import StorageConfig
    from domain.base.ports.scheduler_port import SchedulerPort
    from infrastructure.persistence.json.unit_of_work import JSONUnitOfWork

    # Handle different config types
    if isinstance(config, ConfigurationManager):
        # Try to get scheduler strategy to determine base path
        try:
            from infrastructure.di.container import get_container

            container = get_container()
            scheduler_strategy = container.get(SchedulerPort)
            base_path = scheduler_strategy.get_storage_base_path()
        except Exception:
            # Fallback to configuration if scheduler not available
            storage_config = config.get_typed(StorageConfig)
            json_config = storage_config.json_strategy
            base_path = json_config.base_path

        # Extract JSON-specific configuration through StorageConfig
        storage_config = config.get_typed(StorageConfig)
        json_config = storage_config.json_strategy
        filenames = json_config.filenames

        # Handle different storage types (single_file vs split_files)
        if json_config.storage_type == "single_file":
            # For single file, use the same file for all entities
            single_file = filenames.get("single_file", "request_database.json")
            return JSONUnitOfWork(
                data_dir=base_path,
                machine_file=single_file,
                request_file=single_file,
                template_file=single_file,
                create_dirs=True,
            )
        else:
            # For split files, use individual file names
            split_files = filenames.get("split_files", {})
            return JSONUnitOfWork(
                data_dir=base_path,
                machine_file=split_files.get("machines", "machines.json"),
                request_file=split_files.get("requests", "requests.json"),
                template_file=split_files.get("templates", "templates.json"),
                create_dirs=True,
            )
    else:
        # For testing or other scenarios - assume it's a dict with file paths
        return JSONUnitOfWork(
            data_dir=config.get("data_dir", "data"),
            machine_file=config.get("machine_file", "machines.json"),
            request_file=config.get("request_file", "requests.json"),
            template_file=config.get("template_file", "templates.json"),
            create_dirs=True,
        )


def register_json_storage() -> None:
    """
    Register JSON storage type with the storage registry.

    This function registers JSON storage strategy factory with the global
    storage registry, enabling JSON storage to be used through the
    registry pattern.

    CLEAN ARCHITECTURE: Only registers storage strategy, no repository knowledge.
    REGISTRY PATTERN: Creates closure factories that capture configuration at registration time.
    """
    registry = get_storage_registry()
    logger = get_logger(__name__)

    # Check if already registered (idempotent registration)
    if hasattr(registry, "is_registered") and registry.is_registered("json"):
        logger.debug("JSON storage type already registered, skipping")
        return

    try:
        # Get configuration manager at registration time to capture in closure
        from config.manager import get_config_manager

        config_manager = get_config_manager()

        # Create closure factory that captures configuration
        def unit_of_work_factory() -> Any:
            """Parameter-less factory that creates JSONUnitOfWork with captured config."""
            return create_json_unit_of_work(config_manager)

        registry.register_storage(
            storage_type="json",
            strategy_factory=create_json_strategy,
            config_factory=create_json_config,
            unit_of_work_factory=unit_of_work_factory,  # Closure with captured config
        )

        logger.info("Successfully registered JSON storage type with closure factory")

    except ValueError as e:
        # Handle "already registered" errors gracefully
        if "already registered" in str(e):
            logger.debug("JSON storage type already registered: %s", str(e))
        else:
            logger.error("Failed to register JSON storage type: %s", e)
            raise
    except Exception as e:
        logger.error("Failed to register JSON storage type: %s", e)
        raise
