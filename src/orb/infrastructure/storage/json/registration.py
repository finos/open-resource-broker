"""JSON Storage Registration Module.

This module provides registration functions for JSON storage type,
enabling the storage registry pattern for JSON storage.

CLEAN ARCHITECTURE: Only handles storage strategies, no repository knowledge.
"""

from typing import Any

from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.storage.registry import get_storage_registry


def create_json_strategy(config: Any) -> Any:
    """
    Create JSON storage strategy from configuration with metrics injection.

    Args:
        config: Configuration object containing JSON storage settings

    Returns:
        JSONStorageStrategy instance with optional metrics collector
    """
    from orb.infrastructure.storage.json.strategy import JSONStorageStrategy

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

    # Try to inject metrics collector from DI container
    metrics = None
    # Don't try to get container during registration - causes circular dependency
    # Metrics will be injected later if needed

    return JSONStorageStrategy(
        file_path=file_path, create_dirs=True, entity_type="generic", metrics=metrics
    )


def create_json_config(data: dict[str, Any]) -> Any:
    """
    Create JSON storage configuration from data.

    Args:
        data: Configuration data dictionary

    Returns:
        JSON configuration object
    """
    from orb.config.schemas.storage_schema import JsonStrategyConfig

    return JsonStrategyConfig(**data)


def create_json_unit_of_work(config: Any) -> Any:
    """
    Create JSON unit of work with correct configuration extraction.

    Args:
        config: Configuration object (ConfigurationManager or dict)

    Returns:
        JSONUnitOfWork instance with correctly extracted configuration
    """
    from orb.config.manager import ConfigurationManager
    from orb.config.schemas.storage_schema import StorageConfig
    from orb.infrastructure.storage.json.unit_of_work import JSONUnitOfWork

    # Handle different config types
    if isinstance(config, ConfigurationManager):
        # Try to get scheduler strategy to determine base path
        base_path = "data"  # Default fallback
        try:
            # Don't try to get container during registration - causes circular dependency
            # Use configuration fallback instead
            storage_config = config.get_typed(StorageConfig)
            json_config = storage_config.json_strategy
            base_path = json_config.base_path
        except Exception:
            # Fallback to default
            base_path = "data"

        # Extract JSON-specific configuration through StorageConfig
        storage_config = config.get_typed(StorageConfig)
        json_config = storage_config.json_strategy
        filenames = json_config.filenames

        # Handle different storage types (single_file vs split_files)
        if json_config.storage_type == "single_file":
            # For single file, use the same file for all entities
            single_file = filenames.get("single_file", "request_database.json")
            return JSONUnitOfWork(  # type: ignore[abstract]
                data_dir=base_path,
                machine_file=single_file,
                request_file=single_file,
                template_file=single_file,
                create_dirs=True,
                backup_count=json_config.backup_count,
                backup_enabled=json_config.backup_enabled,
            )
        else:
            # For split files, use individual file names
            split_files = filenames.get("split_files", {})
            return JSONUnitOfWork(  # type: ignore[abstract]
                data_dir=base_path,
                machine_file=split_files.get("machines", "machines.json"),
                request_file=split_files.get("requests", "requests.json"),
                template_file=split_files.get("templates", "templates.json"),
                create_dirs=True,
                backup_count=json_config.backup_count,
                backup_enabled=json_config.backup_enabled,
            )
    else:
        # For testing or other scenarios - assume it's a dict with file paths
        return JSONUnitOfWork(  # type: ignore[abstract]
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
    REGISTRY PATTERN: Creates factories that accept configuration at creation time.
    """
    registry = get_storage_registry()
    logger = get_logger(__name__)

    try:
        # Factory that accepts config parameter (no closure capture)
        def unit_of_work_factory(config: Any) -> Any:
            """Factory that creates JSONUnitOfWork with provided config."""
            return create_json_unit_of_work(config)

        registry.register_storage(
            storage_type="json",
            strategy_factory=create_json_strategy,
            config_factory=create_json_config,
            unit_of_work_factory=unit_of_work_factory,  # Accepts config parameter
        )

        logger.info("Successfully registered JSON storage type")

    except Exception as e:
        logger.error("Failed to register JSON storage type: %s", e, exc_info=True)
        raise
