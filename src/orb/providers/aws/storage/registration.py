"""AWS Storage Registration Module.

This module provides registration functions for DynamoDB and Aurora storage types,
enabling the storage registry pattern for AWS storage backends.

CLEAN ARCHITECTURE: Only handles storage strategies, no repository knowledge.
"""

from typing import TYPE_CHECKING, Any, Optional

# Use TYPE_CHECKING to avoid direct infrastructure import
if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort
    from orb.infrastructure.storage.registry import StorageRegistry


def create_dynamodb_strategy(config: Any) -> Any:
    """
    Create DynamoDB storage strategy from configuration.

    Args:
        config: Configuration object containing DynamoDB storage settings

    Returns:
        DynamoDBStorageStrategy instance
    """
    from orb.config.manager import ConfigurationManager
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.aws.configuration.config import AWSProviderConfig
    from orb.providers.aws.storage.config import DynamodbStrategyConfig
    from orb.providers.aws.storage.strategy import DynamoDBStorageStrategy

    if isinstance(config, ConfigurationManager):
        aws_cfg = config.get_typed(AWSProviderConfig)
        dynamodb_cfg = aws_cfg.storage.dynamodb or DynamodbStrategyConfig()  # type: ignore[call-arg]
    else:
        dynamodb_cfg = DynamodbStrategyConfig(**(config if isinstance(config, dict) else {}))  # type: ignore[call-arg]

    region = dynamodb_cfg.region
    profile = dynamodb_cfg.profile
    table_prefix = dynamodb_cfg.table_prefix

    return DynamoDBStorageStrategy(
        logger=LoggingAdapter(__name__),
        aws_client=None,  # Strategy will create its own client
        region=region,
        table_name=f"{table_prefix}-generic",
        profile=profile,
    )


def create_dynamodb_config(data: dict[str, Any]) -> Any:
    """
    Create DynamoDB storage configuration from data.

    Args:
        data: Configuration data dictionary

    Returns:
        DynamoDB configuration object
    """
    from orb.providers.aws.storage.config import DynamodbStrategyConfig

    return DynamodbStrategyConfig(**data)


def create_dynamodb_unit_of_work(config: Any) -> Any:
    """
    Create DynamoDB unit of work with correct configuration extraction.

    Args:
        config: Configuration object (ConfigurationManager or dict)

    Returns:
        DynamoDBUnitOfWork instance with correctly configured AWS client
    """
    from botocore.config import Config

    from orb.providers.aws.session_factory import AWSSessionFactory

    boto_config = Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 3})

    from orb.config.manager import ConfigurationManager
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.aws.storage.config import DynamodbStrategyConfig
    from orb.providers.aws.storage.unit_of_work import DynamoDBUnitOfWork

    _logger = LoggingAdapter(__name__)

    # Handle different config types
    if isinstance(config, ConfigurationManager):
        from orb.providers.aws.configuration.config import AWSProviderConfig

        aws_cfg = config.get_typed(AWSProviderConfig)
        dynamodb_config = aws_cfg.storage.dynamodb or DynamodbStrategyConfig()  # type: ignore[call-arg]

        session = AWSSessionFactory.create_session(
            profile=dynamodb_config.profile if dynamodb_config.profile else None,
            region=dynamodb_config.region,
        )
        aws_client = session.client("dynamodb", config=boto_config)

        return DynamoDBUnitOfWork(  # type: ignore[abstract]
            aws_client=aws_client,
            logger=_logger,
            region=dynamodb_config.region,
            profile=dynamodb_config.profile,
            machine_table=f"{dynamodb_config.table_prefix}-machines",
            request_table=f"{dynamodb_config.table_prefix}-requests",
            template_table=f"{dynamodb_config.table_prefix}-templates",
        )
    else:
        # For testing or other scenarios - assume it's a dict with AWS config
        region = config.get("region") or None
        profile = config.get("profile")
        table_prefix = config.get("table_prefix", "hostfactory")

        session = AWSSessionFactory.create_session(
            profile=profile if profile else None, region=region
        )
        aws_client = session.client("dynamodb", config=boto_config)

        return DynamoDBUnitOfWork(  # type: ignore[abstract]
            aws_client=aws_client,
            logger=_logger,
            region=region,
            profile=profile,
            machine_table=f"{table_prefix}-machines",
            request_table=f"{table_prefix}-requests",
            template_table=f"{table_prefix}-templates",
        )


def register_dynamodb_storage(
    registry: "Optional[StorageRegistry]" = None, logger: "Optional[LoggingPort]" = None
) -> None:
    """
    Register DynamoDB storage type with the storage registry.

    This function registers DynamoDB storage strategy factory with the global
    storage registry, enabling DynamoDB storage to be used through the
    registry pattern.

    CLEAN ARCHITECTURE: Only registers storage strategy, no repository knowledge.

    Args:
        registry: Storage registry instance (optional)
        logger: Logger port for logging (optional)
    """
    if registry is None:
        # Import here to avoid circular dependencies
        from orb.infrastructure.storage.registry import get_storage_registry

        registry = get_storage_registry()

    try:
        registry.register_storage(
            storage_type="dynamodb",
            strategy_factory=create_dynamodb_strategy,
            config_factory=create_dynamodb_config,
            unit_of_work_factory=create_dynamodb_unit_of_work,
        )

        if logger:
            logger.info("Successfully registered DynamoDB storage type")

    except Exception as e:
        if logger:
            logger.error("Failed to register DynamoDB storage type: %s", e, exc_info=True)
        raise


def _build_aurora_connection_string(sql_config: Any) -> str:
    """Build Aurora MySQL connection string from configuration.

    Uses cluster_endpoint if available, falls back to host.
    Appends SSL parameters when ssl_ca is configured.

    Args:
        sql_config: AuroraSqlStrategyConfig instance

    Returns:
        SQLAlchemy-compatible connection string
    """
    host = sql_config.cluster_endpoint if sql_config.cluster_endpoint else sql_config.host
    base = (
        f"mysql+pymysql://{sql_config.username}:{sql_config.password}"
        f"@{host}:{sql_config.port}/{sql_config.name}"
    )
    if sql_config.ssl_ca:
        ssl_verify = "true" if sql_config.ssl_verify else "false"
        base = f"{base}?ssl_ca={sql_config.ssl_ca}&ssl_verify_cert={ssl_verify}"
    return base


def create_aurora_strategy(config: Any) -> Any:
    """Create SQLStorageStrategy configured for Aurora.

    Args:
        config: Configuration object containing sql_strategy (AuroraSqlStrategyConfig)

    Returns:
        SQLStorageStrategy instance
    """
    from orb.config.manager import ConfigurationManager
    from orb.infrastructure.storage.exceptions import StorageError
    from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy
    from orb.providers.aws.configuration.config import AWSProviderConfig

    if isinstance(config, ConfigurationManager):
        aws_cfg = config.get_typed(AWSProviderConfig)
        if aws_cfg.storage.aurora is None:
            raise StorageError(
                "Aurora storage selected but no aurora config found in provider config"
            )
        sql_config = aws_cfg.storage.aurora
        connection_string = _build_aurora_connection_string(sql_config)
    else:
        connection_string = (
            getattr(config, "connection_string", None) or "mysql+pymysql://localhost/orb"
        )

    return SQLStorageStrategy(
        config={"connection_string": connection_string},
        table_name="generic_storage",
        columns={"id": "TEXT PRIMARY KEY", "data": "TEXT"},
    )


def create_aurora_config(data: dict[str, Any]) -> Any:
    """Create AuroraSqlStrategyConfig from a data dictionary.

    Args:
        data: Configuration data dictionary

    Returns:
        AuroraSqlStrategyConfig instance
    """
    from orb.providers.aws.storage.config import AuroraSqlStrategyConfig

    return AuroraSqlStrategyConfig(**data)


def create_aurora_unit_of_work(config: Any) -> Any:
    """Create SQLUnitOfWork with an Aurora-specific SQLAlchemy engine.

    Args:
        config: ConfigurationManager or dict with connection info

    Returns:
        SQLUnitOfWork instance
    """
    from sqlalchemy import create_engine

    from orb.config.manager import ConfigurationManager
    from orb.infrastructure.storage.exceptions import StorageError
    from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork

    if isinstance(config, ConfigurationManager):
        from orb.providers.aws.configuration.config import AWSProviderConfig

        aws_cfg = config.get_typed(AWSProviderConfig)
        if aws_cfg.storage.aurora is None:
            raise StorageError(
                "Aurora storage selected but no aurora config found in provider config"
            )
        aurora_cfg = aws_cfg.storage.aurora
        connection_string = _build_aurora_connection_string(aurora_cfg)
        engine = create_engine(
            connection_string,
            pool_size=aurora_cfg.pool_size,
            max_overflow=aurora_cfg.max_overflow,
            pool_timeout=getattr(aurora_cfg, "pool_timeout", 30),
            pool_recycle=getattr(aurora_cfg, "pool_recycle", 3600),
            echo=getattr(aurora_cfg, "echo", False),
        )
    else:
        connection_string = config.get("connection_string", "mysql+pymysql://localhost/orb")
        engine = create_engine(connection_string)

    return SQLUnitOfWork(engine)  # type: ignore[abstract]


def register_aurora_storage(
    registry: "Optional[StorageRegistry]" = None, logger: "Optional[LoggingPort]" = None
) -> None:
    """Register Aurora storage type with the storage registry.

    Args:
        registry: Storage registry instance (optional, uses global registry if omitted)
        logger: Logger port for logging (optional)
    """
    if registry is None:
        from orb.infrastructure.storage.registry import get_storage_registry

        registry = get_storage_registry()

    try:
        registry.register_storage(
            storage_type="aurora",
            strategy_factory=create_aurora_strategy,
            config_factory=create_aurora_config,
            unit_of_work_factory=create_aurora_unit_of_work,
        )

        if logger:
            logger.info("Successfully registered Aurora storage type")

    except Exception as e:
        if logger:
            logger.error("Failed to register Aurora storage type: %s", e, exc_info=True)
        raise
