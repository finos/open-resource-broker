"""Configuration loading utilities."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, TypeVar

from config.schemas import AppConfig, validate_config
from domain.base.exceptions import ConfigurationError

if TYPE_CHECKING:
    from config.managers.configuration_manager import ConfigurationManager

T = TypeVar("T")


# Use lazy import to avoid circular dependency
def _get_logger():
    """Lazy import of logger to avoid circular dependency."""
    from infrastructure.logging.logger import get_logger

    return get_logger(__name__)


# Create logger instance lazily
logger = None


def get_config_logger():
    """Get logger instance with lazy initialization."""
    global logger
    if logger is None:
        logger = _get_logger()
    return logger


class ConfigurationLoader:
    """
    Configuration loader that handles loading from multiple sources.

    This class is responsible for loading configuration from:
    - Environment variables
    - Configuration files
    - Legacy configuration files
    - Default values

    It provides a centralized interface for loading configuration with:
    - Type safety through dataclasses
    - Support for legacy and new configuration formats
    - Environment variable overrides
    - Configuration validation
    """

    # Environment variable mappings
    ENV_MAPPING = {
        "AWS_REGION": ("aws", "region"),
        "AWS_PROFILE": ("aws", "profile"),
        "AWS_ROLE_ARN": ("aws", "role_arn"),
        "AWS_ACCESS_KEY_ID": ("aws", "access_key_id"),
        "AWS_SECRET_ACCESS_KEY": ("aws", "secret_access_key"),
        "AWS_SESSION_TOKEN": ("aws", "session_token"),
        "AWS_ENDPOINT_URL": ("aws", "endpoint_url"),
        # Symphony AWS configuration fields
        "AWS_CREDENTIAL_FILE": ("aws", "credential_file"),
        "AWS_KEY_FILE": ("aws", "key_file"),
        "AWS_PROXY_HOST": ("aws", "proxy_host"),
        "AWS_PROXY_PORT": ("aws", "proxy_port"),
        "AWS_CONNECT_TIMEOUT": ("aws", "aws_connect_timeout"),
        "AWS_CONNECTION_TIMEOUT_MS": ("aws", "connection_timeout_ms"),
        "AWS_REQUEST_RETRY_ATTEMPTS": ("aws", "request_retry_attempts"),
        "AWS_INSTANCE_PENDING_TIMEOUT_SEC": ("aws", "instance_pending_timeout_sec"),
        "AWS_DESCRIBE_REQUEST_RETRY_ATTEMPTS": (
            "aws",
            "describe_request_retry_attempts",
        ),
        "AWS_DESCRIBE_REQUEST_INTERVAL": ("aws", "describe_request_interval"),
        # Logging configuration
        "LOG_LEVEL": ("logging", "level"),
        "LOG_FILE": ("logging", "file_path"),
        "LOG_CONSOLE_ENABLED": ("logging", "console_enabled"),
        "ACCEPT_PROPAGATED_LOG_SETTING": ("logging", "accept_propagated_setting"),
        # Events configuration
        "EVENTS_STORE_TYPE": ("events", "store_type"),
        "EVENTS_STORE_PATH": ("events", "store_path"),
        "EVENTS_PUBLISHER_TYPE": ("events", "publisher_type"),
        "EVENTS_ENABLE_LOGGING": ("events", "enable_logging"),
        # Application configuration
        "ENVIRONMENT": ("environment",),
        "DEBUG": ("debug",),
        "REQUEST_TIMEOUT": ("request_timeout",),
        "MAX_MACHINES_PER_REQUEST": ("max_machines_per_request",),
    }

    # Default configuration file name
    DEFAULT_CONFIG_FILENAME = "default_config.json"

    @classmethod
    def load(
        cls,
        config_path: Optional[str] = None,
        config_manager: Optional[ConfigurationManager] = None,
    ) -> Dict[str, Any]:
        """
        Load configuration from multiple sources with correct precedence.

        Precedence order (highest to lowest):
        1. Environment variables (highest precedence)
        2. Explicit config file (if config_path provided)
        3. Scheduler-provided config directory/config.json
        4. config/config.json (fallback)
        5. Legacy configuration (awsprov_config.json, awsprov_templates.json)
        6. default_config.json (lowest precedence)

        Args:
            config_path: Optional path to configuration file

        Returns:
            Loaded configuration dictionary

        Raises:
            ConfigurationError: If configuration loading fails
        """
        # Start with default configuration (lowest precedence)
        config = cls._load_default_config()

        # Load main config.json with correct precedence (scheduler config dir first,
        # then config/)
        main_config = cls._load_config_file(
            "conf", "config.json", required=False, config_manager=config_manager
        )
        if main_config:
            cls._merge_config(config, main_config)
            get_config_logger().info("Loaded main configuration")

        # Load explicit configuration file if provided (higher precedence)
        if config_path:
            get_config_logger().debug("Loading user configuration from: %s", config_path)

            # Extract filename from path for file resolution
            filename = os.path.basename(config_path) if config_path else "config.json"

            file_config = cls._load_config_file(
                "conf",
                filename,
                explicit_path=config_path,
                required=False,
                config_manager=config_manager,
            )
            if file_config:
                cls._merge_config(config, file_config)
                get_config_logger().info("Loaded user configuration")
            else:
                get_config_logger().warning("User configuration file not found: %s", config_path)

        # Override with environment variables (highest precedence)
        cls._load_from_env(config, config_manager)

        # Expand environment variables in the final configuration
        from config.utils.env_expansion import expand_config_env_vars

        config = expand_config_env_vars(config)

        return config

    @classmethod
    def _load_default_config(cls) -> dict[str, Any]:
        """
        Load default configuration from file.

        First tries to load from scheduler config directory, then falls back to local config.

        Returns:
            Default configuration dictionary
        """
        get_config_logger().debug("Loading default configuration")

        # Use file loading method
        config = cls._load_config_file(
            "conf", cls.DEFAULT_CONFIG_FILENAME, required=False, config_manager=None
        )

        if config:
            get_config_logger().info("Loaded default configuration successfully")
            return config
        else:
            get_config_logger().warning(
                "Failed to load default configuration from any location. Using empty configuration."
            )
            return {}

    @classmethod
    def create_app_config(cls, config: dict[str, Any]) -> AppConfig:
        """
        Create typed AppConfig from configuration dictionary using Pydantic.

        Args:
            config: Configuration dictionary

        Returns:
            Typed AppConfig object

        Raises:
            ConfigurationError: If configuration is invalid
        """
        try:
            # Validate and create AppConfig using Pydantic
            app_config = validate_config(config)
            get_config_logger().debug("Configuration validated with Pydantic")
            return app_config

        except ValueError as e:
            # Convert Pydantic validation errors to ConfigurationError
            raise ConfigurationError("App", f"Configuration validation failed: {e!s}")
        except KeyError as e:
            raise ConfigurationError("App", f"Missing required configuration: {e!s}")
        except Exception as e:
            raise ConfigurationError("App", f"Failed to create typed configuration: {e!s}")

    @classmethod
    def _load_from_file(cls, config_path: str) -> Optional[Dict[str, Any]]:
        """
        Load configuration from file.

        Args:
            config_path: Path to configuration file

        Returns:
            Loaded configuration or None if file not found

        Raises:
            ConfigurationError: If file cannot be loaded
        """
        try:
            path = Path(config_path)
            if not path.exists():
                get_config_logger().warning("Configuration file not found: %s", config_path)
                return None

            with path.open() as f:
                return json.load(f)

        except json.JSONDecodeError as e:
            raise ConfigurationError(f"Invalid JSON in configuration file: {e!s}")
        except Exception as e:
            raise ConfigurationError(f"Failed to load configuration file: {e!s}")

    @classmethod
    def _load_config_file(
        cls,
        file_type: str,
        filename: str,
        explicit_path: Optional[str] = None,
        required: bool = False,
        config_manager: Optional[ConfigurationManager] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Centralized method for loading any configuration file with consistent priority:
        1. Explicit path (if provided and contains directory)
        2. Scheduler-provided directory + filename (if file exists)
        3. Default directory + filename

        Args:
            file_type: Type of file ('conf', 'template', 'legacy', 'log', 'work', 'events', 'snapshots')
            filename: Name of the file
            explicit_path: Explicit path provided by user (optional)
            required: Whether the file is required (affects logging level)

        Returns:
            Loaded configuration or None if file not found

        Raises:
            ConfigurationError: If file cannot be loaded
        """
        get_config_logger().debug(
            "Loading config file: type=%s, filename=%s, explicit_path=%s",
            file_type,
            filename,
            explicit_path,
        )

        # Resolve the file path using centralized logic
        # In practice, this would be refactored to use a static method or utility
        resolved_path = cls._resolve_file_path(file_type, filename, explicit_path, config_manager)

        if resolved_path:
            get_config_logger().info("Loading %s configuration from: %s", file_type, resolved_path)
            return cls._load_from_file(resolved_path)
        else:
            if required:
                get_config_logger().error(
                    "Required %s configuration file not found: %s", file_type, filename
                )
            else:
                get_config_logger().debug(
                    "Optional %s configuration file not found: %s", file_type, filename
                )
            return None

    @classmethod
    def _resolve_file_path(
        cls,
        file_type: str,
        filename: str,
        explicit_path: Optional[str] = None,
        config_manager: Optional[ConfigurationManager] = None,
    ) -> Optional[str]:
        """
        Resolve file path using centralized logic (static version of ConfigurationManager.resolve_file).

        Args:
            file_type: Type of file ('conf', 'template', 'legacy', 'log', 'work', 'events', 'snapshots')
            filename: Name of the file
            explicit_path: Explicit path provided by user (optional)
            config_manager: Configuration manager for scheduler directory resolution (optional)

        Returns:
            Resolved file path or None if not found
        """
        get_config_logger().debug(
            "Resolving file path: type=%s, filename=%s, explicit_path=%s",
            file_type,
            filename,
            explicit_path,
        )

        # 1. If explicit path provided and contains directory, use it directly
        if explicit_path and os.path.dirname(explicit_path):
            get_config_logger().debug("Using explicit path with directory: %s", explicit_path)
            return explicit_path if os.path.exists(explicit_path) else None

        # If explicit_path is just a filename, use it as the filename
        if explicit_path and not os.path.dirname(explicit_path):
            filename = explicit_path
            get_config_logger().debug("Using explicit filename: %s", filename)

        # 2. Try scheduler-provided directory + filename
        try:
            scheduler_dir = cls._get_scheduler_directory(file_type, config_manager)
            if scheduler_dir:
                scheduler_path = os.path.join(scheduler_dir, filename)
                get_config_logger().debug("Using scheduler directory path: %s", scheduler_path)
                return scheduler_path
        except Exception as e:
            get_config_logger().debug("Failed to get scheduler directory: %s", e)

        # 3. Fall back to default directory + filename
        default_dirs = {
            "conf": "config",
            "template": "config",
            "legacy": "config",
            "log": "logs",
            "work": "data",
            "events": "events",
            "snapshots": "snapshots",
        }

        default_dir = default_dirs.get(file_type, "config")

        # Build path relative to working directory (not package location)
        project_root = os.getcwd()
        fallback_path = os.path.join(project_root, default_dir, filename)

        # Always return the fallback path, even if file doesn't exist
        # This allows the caller to decide whether to create the file or handle
        # the missing file
        get_config_logger().debug("Using fallback path: %s", fallback_path)
        return fallback_path

    @classmethod
    def _load_from_env(
        cls, config: dict[str, Any], config_manager: Optional[ConfigurationManager] = None
    ) -> None:
        """
        Load configuration from environment variables.

        Args:
            config: Configuration dictionary to update
        """
        # Direct environment variables
        for env_var, config_path in cls.ENV_MAPPING.items():
            if env_var in os.environ:
                value = cls._convert_value(os.environ[env_var])
                current = config
                for i, key in enumerate(config_path):
                    if i == len(config_path) - 1:
                        current[key] = value
                    else:
                        current = current.setdefault(key, {})

        # Process scheduler-provided directory overrides
        cls._process_scheduler_directories(config, config_manager)

        get_config_logger().debug("Loaded configuration from environment variables")

    @classmethod
    def _process_scheduler_directories(
        cls, config: dict[str, Any], config_manager: Optional[ConfigurationManager] = None
    ) -> None:
        """
        Process scheduler-provided directory overrides for logging and storage.

        Args:
            config: Configuration dictionary to update
            config_manager: Configuration manager with scheduler access (optional)
        """
        # Get directories from scheduler
        try:
            scheduler_dir = cls._get_scheduler_directory("work", config_manager)
            logs_dir = cls._get_scheduler_directory("log", config_manager)

            # Set up logging path
            if logs_dir:
                config.setdefault("logging", {})["file_path"] = os.path.join(logs_dir, "app.log")
                get_config_logger().debug(
                    "Set logging file_path to %s", os.path.join(logs_dir, "app.log")
                )

            # Set up storage paths
            if scheduler_dir:
                # Update JSON storage strategy
                storage = config.setdefault("storage", {})
                json_strategy = storage.setdefault("json_strategy", {})
                json_strategy["base_path"] = scheduler_dir
                get_config_logger().debug("Set JSON storage base_path to %s", scheduler_dir)

                # Update SQL storage strategy if using SQLite
                sql_strategy = storage.setdefault("sql_strategy", {})
                if sql_strategy.get("type", "sqlite") == "sqlite":
                    sql_strategy["name"] = os.path.join(scheduler_dir, "database.db")
                    get_config_logger().debug(
                        "Set SQLite database path to %s", os.path.join(scheduler_dir, "database.db")
                    )
        except Exception as e:
            get_config_logger().debug("Could not get scheduler directories: %s", e)

    @classmethod
    def _convert_value(cls, value: str) -> Any:
        """
        Convert string values to appropriate types.

        Args:
            value: String value to convert

        Returns:
            Converted value
        """
        # Try to convert to boolean
        if value.lower() in ("true", "false"):
            return value.lower() == "true"

        # Try to convert to integer
        from contextlib import suppress

        with suppress(ValueError):
            return int(value)

        # Try to convert to float
        with suppress(ValueError):
            return float(value)

        # Try to convert to JSON
        with suppress(json.JSONDecodeError):
            return json.loads(value)

        # Return as string if no conversion possible
        return value

    @classmethod
    def _merge_config(cls, base: dict[str, Any], update: dict[str, Any]) -> None:
        """
        Merge update configuration into base configuration.

        Arrays are replaced entirely, not merged element by element.

        Args:
            base: Base configuration to update
            update: Update configuration
        """
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                # Deep merge for dictionaries
                cls._merge_config(base[key], value)
            else:
                # Replace for all other types (including arrays)
                base[key] = value

    @classmethod
    def _deep_copy(cls, obj: dict[str, Any]) -> dict[str, Any]:
        """
        Create a deep copy of a dictionary.

        Args:
            obj: Dictionary to copy

        Returns:
            Deep copy of dictionary
        """
        return json.loads(json.dumps(obj))

    @classmethod
    def _get_scheduler_directory(
        cls, file_type: str, config_manager: Optional[ConfigurationManager] = None
    ) -> Optional[str]:
        """
        Get directory path from scheduler port for the given file type.

        Args:
            file_type: Type of file ('conf', 'work', 'log', etc.)
            config_manager: Configuration manager with scheduler access (optional)

        Returns:
            Directory path from scheduler or None if not available
        """
        if config_manager:
            return config_manager._get_scheduler_directory(file_type)

        # During bootstrap, check environment variables directly
        import os

        if file_type in ["conf", "template", "legacy"]:
            confdir = os.environ.get("HF_PROVIDER_CONFDIR")
            if confdir:
                return confdir
            workdir = os.environ.get("HF_PROVIDER_WORKDIR")
            if workdir:
                return os.path.join(workdir, "config")
        elif file_type == "log":
            logdir = os.environ.get("HF_PROVIDER_LOGDIR")
            if logdir:
                return logdir
            workdir = os.environ.get("HF_PROVIDER_WORKDIR")
            if workdir:
                return os.path.join(workdir, "logs")
        elif file_type in ["work", "data"]:
            workdir = os.environ.get("HF_PROVIDER_WORKDIR")
            if workdir:
                return workdir

        return None
