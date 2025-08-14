"""DynamoDB Storage Registration Module.

This module provides registration functions for DynamoDB storage type,
enabling the storage registry pattern for DynamoDB persistence.

CLEAN ARCHITECTURE: Only handles storage strategies, no repository knowledge.
"""

from typing import TYPE_CHECKING, Any, Dict

# Use TYPE_CHECKING to avoid direct infrastructure import
if TYPE_CHECKING:
    from src.domain.base.ports import LoggingPort
    from src.infrastructure.registry.storage_registry import StorageRegistry


def create_dynamodb_strategy(config: Any) -> Any:
    """
    Create DynamoDB storage strategy from configuration.

    Args:
        config: Configuration object containing DynamoDB storage settings

    Returns:
        DynamoDBStorageStrategy instance
    """
    from src.providers.aws.persistence.dynamodb.strategy import DynamoDBStorageStrategy

    # Extract configuration parameters
    if hasattr(config, "dynamodb_strategy"):
        dynamodb_config = config.dynamodb_strategy
        region = dynamodb_config.region
        profile = dynamodb_config.profile
        table_prefix = dynamodb_config.table_prefix
    elif hasattr(config, "provider") and hasattr(config.provider, "aws"):
        # Fallback to provider AWS config
        aws_config = config.provider.aws
        region = aws_config.region
        profile = getattr(aws_config, "profile", "default")
        table_prefix = "hostfactory"
    else:
        # Default values
        region = getattr(config, "region", "us-east-1")
        profile = getattr(config, "profile", "default")
        table_prefix = "hostfactory"

    # Create AWS client (this will be handled by the strategy)
    return DynamoDBStorageStrategy(
        aws_client=None,  # Strategy will create its own client
        region=region,
        table_name=f"{table_prefix}-generic",
        profile=profile,
    )


def create_dynamodb_config(data: Dict[str, Any]) -> Any:
    """
    Create DynamoDB storage configuration from data.

    Args:
        data: Configuration data dictionary

    Returns:
        DynamoDB configuration object
    """
    from src.config.schemas.storage_schema import DynamodbStrategyConfig

    return DynamodbStrategyConfig(**data)


def create_dynamodb_unit_of_work(config: Any) -> Any:
    """
    Create DynamoDB unit of work with correct configuration extraction.

    Args:
        config: Configuration object (ConfigurationManager or dict)

    Returns:
        DynamoDBUnitOfWork instance with correctly configured AWS client
    """
    import boto3

    from src.config.manager import ConfigurationManager
    from src.config.schemas.storage_schema import StorageConfig
    from src.providers.aws.persistence.dynamodb.unit_of_work import DynamoDBUnitOfWork

    # Handle different config types
    if isinstance(config, ConfigurationManager):
        # Extract DynamoDB-specific configuration through StorageConfig
        storage_config = config.get_typed(StorageConfig)
        dynamodb_config = storage_config.dynamodb_strategy

        # Create AWS client with extracted configuration
        session = boto3.Session(
            profile_name=dynamodb_config.profile if dynamodb_config.profile else None
        )
        aws_client = session.client("dynamodb", region_name=dynamodb_config.region)

        return DynamoDBUnitOfWork(
            aws_client=aws_client,
            region=dynamodb_config.region,
            profile=dynamodb_config.profile,
            machine_table=f"{dynamodb_config.table_prefix}-machines",
            request_table=f"{dynamodb_config.table_prefix}-requests",
            template_table=f"{dynamodb_config.table_prefix}-templates",
        )
    else:
        # For testing or other scenarios - assume it's a dict with AWS config
        region = config.get("region", "us-east-1")
        profile = config.get("profile")
        table_prefix = config.get("table_prefix", "hostfactory")

        session = boto3.Session(profile_name=profile if profile else None)
        aws_client = session.client("dynamodb", region_name=region)

        return DynamoDBUnitOfWork(
            aws_client=aws_client,
            region=region,
            profile=profile,
            machine_table=f"{table_prefix}-machines",
            request_table=f"{table_prefix}-requests",
            template_table=f"{table_prefix}-templates",
        )


def register_dynamodb_storage(
    registry: "StorageRegistry" = None, logger: "LoggingPort" = None
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
        from src.infrastructure.registry.storage_registry import get_storage_registry

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
            logger.error(f"Failed to register DynamoDB storage type: {e}")
        raise
