"""SQL connection management components for SQL storage operations."""

from contextlib import contextmanager
from typing import Any, Generator, Optional

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from .resource_manager import StorageResourceManager as ResourceManager


def build_sql_url(
    db_type: str,
    name: str,
    host: str = "localhost",
    port: Optional[int] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> URL:
    """Build the SQLAlchemy URL for a database described by its parts.

    The result is a URL object, so credentials containing reserved characters
    need no escaping and a missing password stays absent. Render it with
    ``render_as_string(hide_password=False)`` only where a string is unavoidable.
    """
    if db_type == "sqlite":
        return URL.create("sqlite", database=name)
    if db_type == "postgresql":
        drivername = "postgresql"
    elif db_type == "mysql":
        drivername = "mysql+pymysql"
    else:
        raise ValueError(f"Unsupported database type: {db_type}")
    return URL.create(
        drivername,
        username=username or None,
        password=password or None,
        host=host,
        port=port or None,
        database=name,
    )


class SQLConnectionManager(ResourceManager):
    """
    SQL connection manager for database operations.

    Handles database connections, connection pooling, and session management.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """
        Initialize SQL connection manager.

        Args:
            config: Database configuration. An ``engine`` entry (an existing
                SQLAlchemy engine) is used as-is and is disposed here only when
                ``owns_engine`` is true; otherwise
                ``connection_string`` or the discrete ``type``/``name``/``host``/...
                fields describe the database.
        """
        super().__init__()
        self.config = config
        self._owns_engine = True
        self.engine: Optional[Engine] = None
        self.session_factory: Optional[sessionmaker] = None

        self.initialize()

    def initialize(self) -> None:
        """Initialize SQLAlchemy engine with connection pooling."""
        if self._initialized:
            return

        self._initialize_engine()
        self._initialized = True

    def cleanup(self) -> None:
        """Close all connections and dispose engine."""
        if self.engine:
            if self._owns_engine:
                self.engine.dispose()
            self.logger.debug("SQL connection manager cleaned up")
        self._initialized = False

    def is_healthy(self) -> bool:
        """Check if the SQL connection manager is healthy."""
        try:
            if not self.engine:
                return False

            with self.get_connection() as conn:
                conn.execute(text("SELECT 1"))
                return True
        except Exception as e:
            self.logger.error("Health check failed: %s", e)
            return False

    def get_connection_info(self) -> dict[str, Any]:
        """Get SQL connection information."""
        info = {
            "type": "sql",
            "database_type": self.config.get("type", "unknown"),
            "initialized": self._initialized,
            "healthy": self.is_healthy() if self._initialized else False,
        }

        if self.engine:
            info.update(
                {
                    "pool_size": getattr(self.engine.pool, "size", "N/A"),
                    "checked_out_connections": getattr(self.engine.pool, "checkedout", "N/A"),
                    "overflow_connections": getattr(self.engine.pool, "overflow", "N/A"),
                }
            )

        return info

    def _initialize_engine(self) -> None:
        """Initialize SQLAlchemy engine with connection pooling."""
        try:
            # An engine supplied by the caller is used as-is so every consumer
            # shares one engine (and, for in-memory SQLite, one database).
            supplied_engine = self.config.get("engine")
            if supplied_engine is not None:
                self.engine = supplied_engine
                # Disposed on cleanup only when the caller says the engine is ours
                self._owns_engine = self.config.get("owns_engine") is True
                self.session_factory = sessionmaker(bind=self.engine)
                self.logger.info("Initialized connection manager from supplied engine")
                return

            # Fast-path: use connection_string directly when provided
            if "connection_string" in self.config:
                connection_string = self.config["connection_string"]
                self.engine = create_engine(connection_string, pool_pre_ping=True)
                self.session_factory = sessionmaker(bind=self.engine)
                self.logger.info("Initialized connection manager from connection_string")
                return

            db_type = self.config.get("type", "sqlite")

            if db_type == "sqlite":
                db_path = self.config.get("name")
                if not db_path:
                    from orb.domain.base.exceptions import ConfigurationError

                    raise ConfigurationError(
                        "SQLite storage needs a database name (storage.sql_strategy.name) "
                        "or a connection_string; there is no default database file."
                    )
                connection_string = f"sqlite:///{db_path}"
                timeout = self.config.get("connection_timeout", 30)

                # Cross-process write serialization for SQLite
                # ---------------------------------------------
                # Unlike the JSON backend (which must reimplement cross-process
                # write serialization with an fcntl.flock on a sibling .lock
                # file), SQLite provides this natively. Its built-in
                # database-level file locking escalates a writer to a RESERVED
                # then EXCLUSIVE lock, so at most one process writes at a time
                # on the same host; other writers block until it commits. The
                # LockManager("simple") on the SQL strategy only serializes
                # threads within THIS process — SQLite's own file locking is
                # what serializes writers ACROSS processes.
                #
                # ``timeout`` (default 30s) sets SQLite's busy-timeout so a
                # writer waiting on another process's lock retries rather than
                # failing immediately with "database is locked". WAL mode is
                # deliberately not forced here: the default rollback-journal
                # mode already serializes cross-process writes correctly, and
                # the in-memory (":memory:") databases used in tests do not
                # support a persistent WAL file.
                self.engine = create_engine(
                    connection_string,
                    echo=self.config.get("echo", False),
                    pool_pre_ping=True,
                    connect_args={
                        "check_same_thread": False,
                        "timeout": timeout,
                    },  # SQLite specific
                )

            elif db_type == "postgresql":
                host = self.config.get("host", "localhost")
                port = self.config.get("port", 5432)
                database = self.config.get("name", "database")
                username = self.config.get("username", "user")
                password = self.config.get("password", "")

                connection_string = build_sql_url(
                    "postgresql", database, host, port, username, password
                )

                self.engine = create_engine(
                    connection_string,
                    echo=self.config.get("echo", False),
                    poolclass=QueuePool,
                    pool_size=self.config.get("pool_size", 10),
                    max_overflow=self.config.get("max_overflow", 20),
                    pool_pre_ping=True,
                )

            elif db_type == "mysql":
                host = self.config.get("host", "localhost")
                port = self.config.get("port", 3306)
                database = self.config.get("name", "database")
                username = self.config.get("username", "user")
                password = self.config.get("password", "")

                connection_string = build_sql_url("mysql", database, host, port, username, password)

                self.engine = create_engine(
                    connection_string,
                    echo=self.config.get("echo", False),
                    poolclass=QueuePool,
                    pool_size=self.config.get("pool_size", 10),
                    max_overflow=self.config.get("max_overflow", 20),
                    pool_pre_ping=True,
                )

            else:
                raise ValueError(f"Unsupported database type: {db_type}")

            # Create session factory
            self.session_factory = sessionmaker(bind=self.engine)

            self.logger.info("Initialized %s connection manager", db_type)

        except Exception as e:
            self.logger.error("Failed to initialize connection manager: %s", e)
            raise

    @contextmanager
    def get_session(self) -> Generator[Any, None, None]:
        """
        Get database session with automatic cleanup.

        Yields:
            SQLAlchemy session
        """
        if not self.session_factory:
            raise RuntimeError("Connection manager not initialized")

        session = self.session_factory()
        try:
            yield session
        except Exception as e:
            session.rollback()
            self.logger.error("Session error, rolling back: %s", e)
            raise
        finally:
            session.close()

    @contextmanager
    def get_connection(self) -> Generator[Any, None, None]:
        """
        Get raw database connection.

        Yields:
            SQLAlchemy connection
        """
        if not self.engine:
            raise RuntimeError("Engine not initialized")

        connection = self.engine.connect()
        try:
            yield connection
        finally:
            connection.close()

    def execute_query(self, query: str, params: Optional[dict[str, Any]] = None) -> Any:
        """
        Execute raw SQL query.

        Args:
            query: SQL query string
            params: Query parameters

        Returns:
            Query result (for SELECT) or None (for DDL/DML)
        """
        from sqlalchemy import text

        with self.get_connection() as conn:
            result = conn.execute(text(query), params or {})

            # Only fetch results for SELECT queries
            if query.strip().upper().startswith("SELECT"):
                return result.fetchall()
            else:
                # For DDL/DML queries, just return None
                return None

    def create_tables(self, table_definitions: dict[str, str]) -> None:
        """
        Create tables from definitions.

        Args:
            table_definitions: Dictionary of table_name -> CREATE TABLE SQL
        """
        with self.get_connection() as conn:
            for table_name, create_sql in table_definitions.items():
                try:
                    conn.execute(create_sql)
                    self.logger.debug("Created table: %s", table_name)
                except Exception as e:
                    self.logger.warning(
                        "Table %s creation failed (may already exist): %s",
                        table_name,
                        e,
                    )

    def table_exists(self, table_name: str) -> bool:
        """
        Check if table exists.

        Args:
            table_name: Name of table to check

        Returns:
            True if table exists, False otherwise
        """
        from sqlalchemy import text

        try:
            with self.get_connection() as conn:
                # Use database-specific query
                db_type = self.config.get("type", "sqlite")

                if db_type == "sqlite":
                    result = conn.execute(
                        text(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"
                        ),
                        {"table_name": table_name},
                    )
                elif db_type == "postgresql":
                    result = conn.execute(
                        text("SELECT tablename FROM pg_tables WHERE tablename = :table_name"),
                        {"table_name": table_name},
                    )
                elif db_type == "mysql":
                    result = conn.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables WHERE table_name = :table_name"
                        ),
                        {"table_name": table_name},
                    )
                else:
                    return False

                return result.fetchone() is not None

        except Exception as e:
            self.logger.error("Failed to check table existence: %s", e)
            return False

    def get_engine(self) -> Engine:
        """Get SQLAlchemy engine."""
        if not self.engine:
            raise RuntimeError("Engine not initialized")
        return self.engine

    def close(self) -> None:
        """Close all connections and dispose engine (alias for cleanup)."""
        self.cleanup()

    def get_resource(self, resource_name: str) -> Any:
        """Get a managed resource by name."""
        if resource_name == "engine":
            return self.engine
        if resource_name == "session_factory":
            return self.session_factory
        raise KeyError(f"Unknown resource: {resource_name}")
