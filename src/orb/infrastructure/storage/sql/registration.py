"""SQL Storage Registration Module.

This module provides registration functions for SQL storage type,
enabling the storage registry pattern for SQL storage.

CLEAN ARCHITECTURE: Only handles storage strategies, no repository knowledge.
"""

import threading
from typing import Any

from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.storage.registry import get_storage_registry
from orb.infrastructure.storage.sql.entities import DEFAULT_ENTITY, create_entity_strategy


def _sql_settings(config: Any) -> tuple[Any, Any]:
    """Return (sql_strategy config, database config) for a configuration source.

    Every source resolves to the configuration the repositories use, so all
    consumers open the same database. Accepted shapes:

    * a ``StorageConfig``-like object (has ``sql_strategy``);
    * an application config object (has ``storage``);
    * a configuration object with ``get_typed`` (``ConfigurationManager`` or the
      ``ConfigurationPort`` adapter), read as ``StorageConfig`` so the loader's
      resolved database location applies;
    * the dumped application config ``{"storage": {"sql_strategy": {...}}}`` or a
      bare ``{"sql_strategy": {...}}`` mapping.

    Anything else (for example an empty mapping) names no database. It raises
    ``ConfigurationError`` rather than falling back to a file in the working
    directory.
    """
    from orb.config.schemas.storage_schema import DatabaseConfig, SqlStrategyConfig, StorageConfig
    from orb.domain.base.exceptions import ConfigurationError

    if hasattr(config, "sql_strategy"):
        return config.sql_strategy, getattr(config, "database", None)

    storage = getattr(config, "storage", None)
    if storage is not None and hasattr(storage, "sql_strategy"):
        return storage.sql_strategy, getattr(storage, "database", None)

    if not isinstance(config, dict) and callable(getattr(config, "get_typed", None)):
        storage_config = config.get_typed(StorageConfig)
        return storage_config.sql_strategy, getattr(storage_config, "database", None)

    if isinstance(config, dict):
        nested = config.get("storage")
        source = nested if isinstance(nested, dict) else config
        raw_sql = source.get("sql_strategy")
        if isinstance(raw_sql, dict):
            raw_db = source.get("database")
            db_config = DatabaseConfig.model_validate(raw_db) if isinstance(raw_db, dict) else None
            return SqlStrategyConfig.model_validate(raw_sql), db_config

    raise ConfigurationError(
        "SQL storage needs a configured database: provide storage.sql_strategy "
        "(or a connection_string); no database location can be derived from the "
        "configuration that was passed."
    )


def create_sql_strategy(config: Any) -> Any:
    """Create the SQL storage strategy for callers that have no entity.

    Health probes use this: it binds to the ``requests`` table, which holds the
    real layout and is only read. Repositories use ``create_sql_entity_strategy``
    through the registry.
    """
    return create_sql_entity_strategy(config, DEFAULT_ENTITY)


def create_sql_entity_strategy(config: Any, entity: str) -> Any:
    """
    Create the SQL storage strategy for one entity table.

    The strategy opens the same database as the SQL unit of work (the one named
    by the configured ``sql_strategy``) and the same typed table, so every entry
    point reads and writes the same rows.

    Args:
        config: Configuration object or dict containing SQL storage settings
        entity: "requests", "machines" or "templates"

    Returns:
        SQLStorageStrategy instance
    """
    connection_string = (
        config.get("connection_string")
        if isinstance(config, dict)
        else getattr(config, "connection_string", None)
    )
    if isinstance(connection_string, str) and connection_string:
        # An explicit URL is used as given
        return create_entity_strategy({"connection_string": connection_string}, entity)

    sql_config, db_config = _sql_settings(config)
    connection_timeout = db_config.connection_timeout if db_config is not None else 30

    return create_entity_strategy(
        {
            "type": sql_config.type,
            "engine": _create_engine_for(sql_config),
            # An engine created here belongs to this strategy, so cleanup disposes
            # it; the shared in-memory engine is never disposed by a strategy
            "owns_engine": not _is_shared_memory(sql_config),
            "connection_timeout": connection_timeout,
        },
        entity,
    )


def create_sql_config(data: dict[str, Any]) -> Any:
    """
    Create SQL storage configuration from data.

    Args:
        data: Configuration data dictionary

    Returns:
        SQL configuration object
    """
    from orb.config.schemas.storage_schema import SqlStrategyConfig

    return SqlStrategyConfig(**data)


def _sqlite_url(name: str) -> Any:
    """SQLite URL for a configured database name.

    ``:memory:`` and absolute paths are used as given, a ``file:`` URI is opened
    as a URI, and a relative name stays relative to the working directory (the
    configuration loader resolves relative names under the scheduler data
    directory before they get here).
    """
    from sqlalchemy.engine import URL

    query = {"uri": "true"} if name.startswith("file:") else {}
    return URL.create("sqlite", database=name, query=query)


def _sqlite_file_path(database: str) -> Any:
    """The file a SQLite database name refers to, or None for in-memory databases.

    Plain names are paths; ``file:`` URIs name their file in the path part
    (``mode=memory`` and ``:memory:`` URIs have none).
    """
    from pathlib import Path
    from urllib.parse import parse_qs, unquote, urlsplit

    if not database.startswith("file:"):
        return Path(database).resolve()

    parts = urlsplit(database)
    path = unquote(parts.path)
    if not path or path == ":memory:" or "memory" in parse_qs(parts.query).get("mode", []):
        return None
    return Path(path).resolve()


def _build_url(sql_config: Any) -> Any:
    """
    Build the SQLAlchemy URL for a SQL storage configuration.

    Components are passed to ``URL.create``, so credentials containing reserved
    characters need no escaping.

    Args:
        sql_config: SQL configuration object

    Returns:
        sqlalchemy.engine.URL
    """
    from orb.infrastructure.storage.components.sql_connection_manager import build_sql_url

    db_type = sql_config.type
    if db_type == "sqlite":
        return _sqlite_url(sql_config.name)
    return build_sql_url(
        db_type,
        sql_config.name,
        sql_config.host,
        sql_config.port,
        sql_config.username,
        sql_config.password,
    )


def _build_connection_string(sql_config: Any) -> str:
    """
    Render the connection string for tools that need text (database migrations).

    The result includes the password; never log it.
    """
    return _build_url(sql_config).render_as_string(hide_password=False)


# In-memory SQLite databases exist only inside one connection, so every consumer of
# ":memory:" in a process (unit of work, repositories, health probes) has to share
# the engine to see the same data. The engine uses a single connection: this is for
# development and tests, not for concurrent workloads.
_SHARED_MEMORY_ENGINES: dict[str, Any] = {}
_SHARED_MEMORY_LOCK = threading.Lock()


def _is_shared_memory(sql_config: Any) -> bool:
    return sql_config.type == "sqlite" and sql_config.name == ":memory:"


def reset_shared_memory_engines() -> None:
    """Dispose the shared in-memory engines (tests start from an empty database)."""
    with _SHARED_MEMORY_LOCK:
        engines = list(_SHARED_MEMORY_ENGINES.values())
        _SHARED_MEMORY_ENGINES.clear()
    for engine in engines:
        engine.dispose()


def _create_engine_for(sql_config: Any) -> Any:
    """Create the engine for a SQL storage configuration."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    url = _build_url(sql_config)
    echo = getattr(sql_config, "echo", False) is True

    if url.get_backend_name() == "sqlite":
        database = url.database or ""
        if database == ":memory:":
            with _SHARED_MEMORY_LOCK:
                engine = _SHARED_MEMORY_ENGINES.get("sqlite:///:memory:")
                if engine is None:
                    engine = create_engine(
                        url,
                        echo=echo,
                        poolclass=StaticPool,
                        connect_args={"check_same_thread": False},
                    )
                    _SHARED_MEMORY_ENGINES["sqlite:///:memory:"] = engine
                return engine
        path = _sqlite_file_path(database)
        if path is not None:
            if not path.exists():
                get_logger(__name__).warning(
                    "SQLite database %s not found; creating a new empty database. If you have "
                    "an existing database, move or rename it to this path or set "
                    "storage.sql_strategy.name to its absolute path.",
                    path,
                )
            path.parent.mkdir(parents=True, exist_ok=True)
        return create_engine(
            url,
            pool_size=sql_config.pool_size,
            max_overflow=sql_config.max_overflow,
            pool_timeout=getattr(sql_config, "pool_timeout", 30),
            pool_recycle=getattr(sql_config, "pool_recycle", 3600),
            echo=echo,
            connect_args={"timeout": getattr(sql_config, "timeout", 30)},
        )

    return create_engine(
        url,
        pool_size=sql_config.pool_size,
        max_overflow=sql_config.max_overflow,
        pool_timeout=getattr(sql_config, "pool_timeout", 30),
        pool_recycle=getattr(sql_config, "pool_recycle", 3600),
        echo=echo,
    )


def create_sql_unit_of_work(config: Any) -> Any:
    """
    Create SQL unit of work with correct configuration extraction.

    Args:
        config: Configuration object (ConfigurationManager or dict)

    Returns:
        SQLUnitOfWork instance with correctly configured engine
    """
    from sqlalchemy import create_engine

    from orb.config.manager import ConfigurationManager
    from orb.config.schemas.storage_schema import StorageConfig
    from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork

    # Handle different config types
    if isinstance(config, ConfigurationManager):
        # Extract SQL-specific configuration through StorageConfig
        storage_config = config.get_typed(StorageConfig)
        return SQLUnitOfWork(_create_engine_for(storage_config.sql_strategy))
    else:
        # Anything else must carry an explicit URL; there is no default database
        connection_string = config.get("connection_string") if isinstance(config, dict) else None
        if not isinstance(connection_string, str) or not connection_string:
            from orb.domain.base.exceptions import ConfigurationError

            raise ConfigurationError(
                "SQL unit of work needs a ConfigurationManager or a connection_string; "
                "no database location can be derived from the configuration that was passed."
            )
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
            display_name="SQL",
            entity_strategy_factory=create_sql_entity_strategy,
        )

        logger.info("Successfully registered SQL storage type")

    except Exception as e:
        logger.error("Failed to register SQL storage type: %s", e, exc_info=True)
        raise
