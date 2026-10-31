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
    from orb.config.manager import ConfigurationManager
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.aws.storage.config import DynamodbStrategyConfig
    from orb.providers.aws.storage.unit_of_work import DynamoDBUnitOfWork

    _logger = LoggingAdapter(__name__)

    # Handle different config types
    if isinstance(config, ConfigurationManager):
        from orb.providers.aws.configuration.config import AWSProviderConfig
        from orb.providers.aws.infrastructure.aws_client import AWSClient

        aws_cfg = config.get_typed(AWSProviderConfig)
        dynamodb_config = aws_cfg.storage.dynamodb or DynamodbStrategyConfig()  # type: ignore[call-arg]

        # ORB AWS wrapper exposes dynamodb_client/dynamodb_resource (lazy boto3),
        # which DynamoDBClientManager consumes directly. ConfigurationManager
        # provides the configuration access AWSClient needs at runtime.
        aws_client = AWSClient(config=config, logger=_logger)  # type: ignore[arg-type]

        collections = config.app_config.naming.collections
        machine_name = collections.get("machines", "machines")
        request_name = collections.get("requests", "requests")
        template_name = collections.get("templates", "templates")

        return DynamoDBUnitOfWork(
            aws_client=aws_client,
            logger=_logger,
            region=dynamodb_config.region,
            profile=dynamodb_config.profile,
            machine_table=f"{dynamodb_config.table_prefix}-{machine_name}",
            request_table=f"{dynamodb_config.table_prefix}-{request_name}",
            template_table=f"{dynamodb_config.table_prefix}-{template_name}",
        )
    else:
        # dict path (tests/direct) - no ConfigurationPort to build AWSClient,
        # so let DynamoDBClientManager self-init its boto3 client/resource.
        region = config.get("region") or None
        profile = config.get("profile")
        table_prefix = config.get("table_prefix", "hostfactory")

        return DynamoDBUnitOfWork(
            aws_client=None,
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
            display_name="DynamoDB",
        )

        if logger:
            logger.info("Successfully registered DynamoDB storage type")

    except Exception as e:
        if logger:
            logger.error("Failed to register DynamoDB storage type: %s", e, exc_info=True)
        raise


_AURORA_NOT_CONFIGURED = (
    "Aurora storage needs the Aurora provider configuration "
    "(provider config storage.aurora) or an explicit connection_string; "
    "no connection could be derived from the configuration that was passed."
)


def _aurora_settings_from_mapping(config: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Find the Aurora settings in a mapping, or None.

    Accepts the AWS provider config (``storage.aurora``), a bare ``aurora``
    section, and the dumped application config, where the settings sit in the
    active (else first enabled) AWS provider instance's ``config.storage.aurora``.
    """
    storage = config.get("storage")
    if isinstance(storage, dict) and isinstance(storage.get("aurora"), dict):
        return storage["aurora"]
    if isinstance(config.get("aurora"), dict):
        return config["aurora"]

    provider_section = config.get("provider")
    providers = provider_section.get("providers") if isinstance(provider_section, dict) else None
    if not isinstance(providers, list):
        return None
    active = provider_section.get("active_provider") if isinstance(provider_section, dict) else None
    candidates = [p for p in providers if isinstance(p, dict) and p.get("type") == "aws"]
    candidates = [p for p in candidates if p.get("enabled", True)]
    candidates.sort(key=lambda p: p.get("name") != active)
    if not candidates:
        return None
    # Only the active (else first enabled) instance counts: another instance's
    # database must never be picked up because this one has none configured.
    provider_config = candidates[0].get("config")
    provider_storage = provider_config.get("storage") if isinstance(provider_config, dict) else None
    if isinstance(provider_storage, dict) and isinstance(provider_storage.get("aurora"), dict):
        return provider_storage["aurora"]
    return None


def _validated_aurora_settings(raw: Any) -> Any:
    """Aurora settings as an ``AuroraSqlStrategyConfig`` that names a complete target.

    A connection needs an endpoint, a database name, a user and a password; none
    of them has a usable default. Errors name the fields only, never the values.
    """
    from pydantic import ValidationError

    from orb.infrastructure.storage.exceptions import StorageError
    from orb.providers.aws.storage.config import AuroraSqlStrategyConfig

    if isinstance(raw, AuroraSqlStrategyConfig):
        settings = raw
    else:
        try:
            settings = AuroraSqlStrategyConfig.model_validate(raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in err['loc']) or 'aurora'}: {err['msg']}"
                for err in exc.errors(include_input=False, include_url=False)
            )
            raise StorageError(f"Aurora storage configuration is invalid ({problems})") from None

    missing = [
        label
        for label, present in (
            ("cluster_endpoint or host", bool(settings.cluster_endpoint or settings.host)),
            ("name", "name" in settings.model_fields_set and bool(settings.name)),
            ("username", bool(settings.username)),
            ("password", bool(settings.password)),
        )
        if not present
    ]
    if missing:
        raise StorageError(
            "Aurora storage configuration is incomplete: set " + ", ".join(missing) + "."
        )
    return settings


def _aurora_url(settings: Any) -> Any:
    """SQLAlchemy URL for validated Aurora settings (credentials need no escaping)."""
    from sqlalchemy.engine import URL

    query: dict[str, str] = {}
    if settings.ssl_ca:
        query = {
            "ssl_ca": settings.ssl_ca,
            "ssl_verify_cert": "true" if settings.ssl_verify else "false",
        }
    return URL.create(
        "mysql+pymysql",
        username=settings.username,
        password=settings.password,
        host=settings.cluster_endpoint or settings.host,
        port=settings.port,
        database=settings.name,
        query=query,
    )


def resolve_aurora_connection(config: Any) -> tuple[Any, Any]:
    """Resolve ``(url, settings)`` for Aurora from any configuration source.

    Every Aurora entry point (repositories, unit of work, health probes) calls
    this, so they all reach the same database. ``settings`` is None when the
    caller supplied an explicit connection string.

    Accepted sources: a mapping or object with ``connection_string``; an object
    with ``get_typed`` (``ConfigurationManager`` or the configuration adapter),
    read as the AWS provider config; the dumped application config; an
    ``AuroraSqlStrategyConfig``.

    Raises:
        StorageError: If the configuration names no complete Aurora target.
            There is no default endpoint.
    """
    from sqlalchemy.engine import make_url

    from orb.infrastructure.storage.exceptions import StorageError
    from orb.providers.aws.storage.config import AuroraSqlStrategyConfig

    connection_string = (
        config.get("connection_string")
        if isinstance(config, dict)
        else getattr(config, "connection_string", None)
    )
    if isinstance(connection_string, str) and connection_string:
        return make_url(connection_string), None

    if isinstance(config, AuroraSqlStrategyConfig):
        settings = _validated_aurora_settings(config)
        return _aurora_url(settings), settings

    if isinstance(config, dict):
        raw = _aurora_settings_from_mapping(config)
        if raw is None:
            raise StorageError(_AURORA_NOT_CONFIGURED)
        settings = _validated_aurora_settings(raw)
        return _aurora_url(settings), settings

    if callable(getattr(config, "get_typed", None)):
        from orb.providers.aws.configuration.config import AWSProviderConfig

        aws_cfg = config.get_typed(AWSProviderConfig)
        if aws_cfg.storage.aurora is None:
            raise StorageError(
                "Aurora storage selected but no aurora config found in provider config"
            )
        settings = _validated_aurora_settings(aws_cfg.storage.aurora)
        return _aurora_url(settings), settings

    raise StorageError(_AURORA_NOT_CONFIGURED)


def _aurora_engine(url: Any, settings: Any) -> Any:
    """The process's engine for an Aurora database, built once per database.

    The repositories, the unit of work and the health probe all reach the
    database through this engine, so they share one connection pool.
    """
    from sqlalchemy import create_engine

    from orb.infrastructure.storage.components.engine_registry import get_or_create_engine
    from orb.infrastructure.storage.components.sql_connection_manager import shared_engine_for_url

    if settings is None:
        return shared_engine_for_url(url, pool_pre_ping=True)

    return get_or_create_engine(
        url,
        lambda u: create_engine(
            u,
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            pool_timeout=getattr(settings, "pool_timeout", 30),
            pool_recycle=getattr(settings, "pool_recycle", 3600),
            pool_pre_ping=True,
            echo=getattr(settings, "echo", False),
            hide_parameters=True,
        ),
    )


def create_aurora_strategy(config: Any) -> Any:
    """Create the Aurora SQL strategy for callers that have no entity (health probes)."""
    from orb.infrastructure.storage.sql.entities import DEFAULT_ENTITY

    return create_aurora_entity_strategy(config, DEFAULT_ENTITY)


def create_aurora_entity_strategy(config: Any, entity: str) -> Any:
    """Create the typed-table SQLStorageStrategy for one entity on Aurora.

    Args:
        config: Any source ``resolve_aurora_connection`` accepts
        entity: "requests", "machines" or "templates"

    Returns:
        SQLStorageStrategy instance

    Raises:
        StorageError: If no Aurora connection can be derived from ``config``
    """
    from orb.infrastructure.storage.sql.entities import create_entity_strategy

    url, settings = resolve_aurora_connection(config)
    engine = _aurora_engine(url, settings)
    return create_entity_strategy(
        {
            "type": url.get_backend_name(),
            "engine": engine,
            # The registry owns shared engines; a private in-memory one is ours
            "owns_engine": (url.database or ":memory:") == ":memory:"
            and url.get_backend_name() == "sqlite",
        },
        entity,
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
        config: Any source ``resolve_aurora_connection`` accepts

    Returns:
        SQLUnitOfWork instance

    Raises:
        StorageError: If no Aurora connection can be derived from ``config``
    """
    from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork

    url, settings = resolve_aurora_connection(config)
    return SQLUnitOfWork(_aurora_engine(url, settings))


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
            display_name="Aurora",
            entity_strategy_factory=create_aurora_entity_strategy,
        )

        if logger:
            logger.info("Successfully registered Aurora storage type")

    except Exception as e:
        if logger:
            logger.error("Failed to register Aurora storage type: %s", e, exc_info=True)
        raise
