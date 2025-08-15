"""SQL Storage Registration Module.

This module provides registration functions for SQL storage type,
enabling the storage registry pattern for SQL persistence.

CLEAN ARCHITECTURE: Only handles storage strategies, no repository knowledge.
"""

from typing import Any, Dict

from infrastructure.logging.logger import get_logger
from infrastructure.registry.storage_registry import get_storage_registry


def create_sql_strategy(config: Any) -> Any:
    """
    Create SQL storage strategy from configuration.

    Args:
        config: Configuration object containing SQL storage settings

    Returns:
        SQLStorageStrategy instance
    """
    from infrastructure.persistence.sql.strategy import SQLStorageStrategy

    # Extract configuration parameters
    if hasattr(config, "sql_strategy"):
        sql_config = config.sql_strategy
        connection_string = _build_connection_string(sql_config)
    else:
        # Fallback for simple config
        connection_string = getattr(config, "connection_string", "sqlite:///data.db")

    return SQLStorageStrategy(
        connection_string=connection_string,
        table_name="generic_storage",
        columns={"id": "TEXT PRIMARY KEY", "data": "TEXT"},
    )


def create_sql_config(data: Dict[str, Any]) -> Any:
    """
    Create SQL storage configuration from data.

    Args:
        data: Configuration data dictionary

    Returns:
        SQL configuration object
    """
    from config.schemas.storage_schema import SqlStrategyConfig

    return SqlStrategyConfig(**data)


def _build_connection_string(sql_config: Any) -> str:
    """
    Build SQL connection string from configuration.

    Args:
        sql_config: SQL configuration object

    Returns:
        Connection string
    """
    db_type = sql_config.type

    if db_type == "sqlite":
        return f"sqlite:///{sql_config.name}"
    elif db_type == "postgresql":
        return f"postgresql://{sql_config.username}:{sql_config.password}@{sql_config.host}:{sql_config.port}/{sql_config.name}"
    elif db_type == "mysql":
        return f"mysql://{sql_config.username}:{sql_config.password}@{sql_config.host}:{sql_config.port}/{sql_config.name}"
    elif db_type == "aurora":
        if sql_config.cluster_endpoint:
            host = sql_config.cluster_endpoint
        else:
            host = sql_config.host
        return f"mysql://{sql_config.username}:{sql_config.password}@{host}:{sql_config.port}/{sql_config.name}"
    else:
        raise ValueError(f"Unsupported database type: {db_type}")


def create_sql_unit_of_work(config: Any) -> Any:
    """
    Create SQL unit of work with correct configuration extraction.

    Args:
        config: Configuration object (ConfigurationManager or dict)

    Returns:
        SQLUnitOfWork instance with correctly configured engine
    """
    from sqlalchemy import create_engine

    from config.manager import ConfigurationManager
    from config.schemas.storage_schema import StorageConfig
    from infrastructure.persistence.sql.unit_of_work import SQLUnitOfWork

    # Handle different config types
    if isinstance(config, ConfigurationManager):
        # Extract SQL-specific configuration through StorageConfig
        storage_config = config.get_typed(StorageConfig)
        sql_config = storage_config.sql_strategy

        # Build connection string and create engine
        connection_string = _build_connection_string(sql_config)
        engine = create_engine(
            connection_string,
            pool_size=sql_config.pool_size,
            max_overflow=sql_config.max_overflow,
            pool_timeout=sql_config.pool_timeout,
            pool_recycle=sql_config.pool_recycle,
            echo=sql_config.echo,
        )

        return SQLUnitOfWork(engine)
    else:
        # For testing or other scenarios - assume it's a dict with connection info
        connection_string = config.get("connection_string", "sqlite:///data/test.db")
        engine = create_engine(connection_string)
        return SQLUnitOfWork(engine)


def register_sql_storage() -> None:
    """
    Register SQL storage type with the storage registry.

    This function registers SQL storage strategy factory with the global
    storage registry, enabling SQL storage to be used through the
    registry pattern.

    CLEAN ARCHITECTURE: Only registers storage strategy, no repository knowledge.
    """
    registry = get_storage_registry()
    logger = get_logger(__name__)

    try:
        registry.register_storage(
            storage_type="sql",
            strategy_factory=create_sql_strategy,
            config_factory=create_sql_config,
            unit_of_work_factory=create_sql_unit_of_work,
        )

        logger.info("Successfully registered SQL storage type")

    except Exception as e:
        logger.error(f"Failed to register SQL storage type: {e}")
        raise
