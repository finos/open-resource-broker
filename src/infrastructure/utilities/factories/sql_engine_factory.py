"""SQLAlchemy engine factory for creating database engines."""

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from config import SqlStrategyConfig
from config.manager import ConfigurationManager
from infrastructure.logging.logger import get_logger


class SQLEngineFactory:
    """Factory for creating SQL database engines with appropriate configuration."""

    @staticmethod
    def create_engine_from_config(config_manager: ConfigurationManager) -> Engine:
        """
        Create SQLAlchemy engine from configuration manager.

        Args:
            config_manager: Configuration manager

        Returns:
            SQLAlchemy engine

        Raises:
            ValueError: If database type is not supported
        """
        get_logger(__name__)

        # Get SQL strategy configuration
        sql_config = config_manager.get_typed(SqlStrategyConfig)

        # Create engine based on configuration
        return SQLEngineFactory.create_engine(sql_config)

    @staticmethod
    def create_engine(config: SqlStrategyConfig) -> Engine:
        """
        Create SQLAlchemy engine based on configuration.

        This factory supports multiple database types including:
        - SQLite
        - PostgreSQL
        - MySQL
        - Aurora

        Args:
            config: SQL strategy configuration

        Returns:
            SQLAlchemy engine

        Raises:
            ValueError: If database type is not supported
        """
        logger = get_logger(__name__)
        db_type = config.type

        # Create engine with appropriate settings
        engine_kwargs = {
            "echo": False,  # Default to no echo, can be overridden in config
        }

        # Build connection string based on database type
        if db_type == "sqlite":
            db_path = config.name

            # Ensure directory exists
            if not db_path.startswith(":memory:"):
                db_dir = os.path.dirname(db_path)
                if db_dir:
                    Path(db_dir).mkdir(parents=True, exist_ok=True)

            connection_string = f"sqlite:///{db_path}"
            logger.debug(f"Creating SQLite engine with connection string: {connection_string}")

            # SQLite-specific settings
            engine_kwargs["connect_args"] = {
                "check_same_thread": False,  # Allow multi-threaded access
                "timeout": config.timeout,  # Connection timeout
            }

        elif db_type == "postgresql":
            host = config.host
            port = config.port
            username = config.username or ""
            password = config.password or ""
            database = config.name

            # Build connection string
            connection_string = f"postgresql://{username}:{password}@{host}:{port}/{database}"
            logger.debug(f"Creating PostgreSQL engine with connection to {host}:{port}/{database}")

            # Add pooling configuration
            engine_kwargs.update(
                {
                    "poolclass": QueuePool,
                    "pool_size": config.pool_size,
                    "max_overflow": config.max_overflow,
                    "pool_timeout": config.timeout,
                    "pool_recycle": 3600,  # Recycle connections after 1 hour
                }
            )

        elif db_type == "mysql":
            host = config.host
            port = config.port
            username = config.username or ""
            password = config.password or ""
            database = config.name

            # Build connection string
            connection_string = f"mysql+pymysql://{username}:{password}@{host}:{port}/{database}"
            logger.debug(f"Creating MySQL engine with connection to {host}:{port}/{database}")

            # Add pooling configuration
            engine_kwargs.update(
                {
                    "poolclass": QueuePool,
                    "pool_size": config.pool_size,
                    "max_overflow": config.max_overflow,
                    "pool_timeout": config.timeout,
                    "pool_recycle": 3600,  # Recycle connections after 1 hour
                }
            )

        elif db_type == "aurora":
            # Aurora can use either the cluster endpoint or a specific host
            host = config.cluster_endpoint or config.host
            port = config.port
            username = config.username or ""
            password = config.password or ""
            database = config.name

            # Build connection string
            connection_string = f"mysql+pymysql://{username}:{password}@{host}:{port}/{database}"
            logger.debug(f"Creating Aurora engine with connection to {host}:{port}/{database}")

            # Add pooling configuration
            engine_kwargs.update(
                {
                    "poolclass": QueuePool,
                    "pool_size": config.pool_size,
                    "max_overflow": config.max_overflow,
                    "pool_timeout": config.timeout,
                    "pool_recycle": 3600,  # Recycle connections after 1 hour
                }
            )

            # Add SSL options if provided
            if config.ssl_ca:
                engine_kwargs["connect_args"] = {
                    "ssl": {"ca": config.ssl_ca, "check_hostname": config.ssl_verify}
                }
                logger.debug(f"Using SSL with CA certificate: {config.ssl_ca}")

        else:
            error_msg = f"Unsupported database type: {db_type}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Create engine
        engine = create_engine(connection_string, **engine_kwargs)

        return engine

    @staticmethod
    def create_session_factory(engine: Engine) -> sessionmaker:
        """
        Create SQLAlchemy session factory.

        Args:
            engine: SQLAlchemy engine

        Returns:
            SQLAlchemy session factory
        """
        return sessionmaker(bind=engine)

    @staticmethod
    def create_session(engine: Engine) -> Session:
        """
        Create SQLAlchemy session.

        Args:
            engine: SQLAlchemy engine

        Returns:
            SQLAlchemy session
        """
        session_factory = SQLEngineFactory.create_session_factory(engine)
        return session_factory()
