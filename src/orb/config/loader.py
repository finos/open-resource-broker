"""Configuration loading utilities."""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, TypeVar

from orb.config.schemas import AppConfig, validate_config
from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.utilities.json_utils import safe_json_dumps, safe_json_loads

if TYPE_CHECKING:
    from orb.config.managers.configuration_manager import ConfigurationManager

T = TypeVar("T")


# Use lazy import to avoid circular dependency
def _get_logger():
    """Lazy import of logger to avoid circular dependency."""
    from orb.infrastructure.logging.logger import get_logger

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

        # Warn if deprecated storage.dynamodb_strategy key is present
        if isinstance(config, dict) and "dynamodb_strategy" in config.get("storage", {}):
            warnings.warn(
                "storage.dynamodb_strategy in config root is deprecated since ORB 2.x. "
                "Move it to provider.providers[N].config.storage.dynamodb. "
                "This key will be removed in ORB 3.0.",
                DeprecationWarning,
                stacklevel=2,
            )

        # Override with environment variables (highest precedence)
        cls._load_from_env(config, config_manager)

        # Expand environment variables in the final configuration
        from orb.config.utils.env_expansion import expand_config_env_vars

        config = expand_config_env_vars(config)

        return config

    @classmethod
    def _load_default_config(cls) -> dict[str, Any]:
        """
        Load default configuration from project config directory.

        Returns:
            Default configuration dictionary
        """
        get_config_logger().debug("Loading default configuration")

        try:
            import json
            from importlib.resources import files

            text = files("orb.config").joinpath(cls.DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8")
            config_data = json.loads(text)
            get_config_logger().info("Loaded default configuration from package data")
            return config_data
        except Exception as e:
            get_config_logger().warning(f"Failed to load default configuration: {e}")
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

        # Resolve the file path using PathResolutionService
        resolved_path = cls._get_path_resolution_service(config_manager).resolve_file_path(
            file_type, filename, explicit_path
        )

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
    def _get_path_resolution_service(cls, config_manager: Optional[ConfigurationManager] = None):
        """
        Create a PathResolutionService wired to the given config_manager.

        Args:
            config_manager: Configuration manager for scheduler directory resolution (optional)

        Returns:
            PathResolutionService instance
        """
        from orb.config.services.path_resolution_service import PathResolutionService

        scheduler_directory_provider = (
            config_manager.get_scheduler_directory if config_manager is not None else None
        )
        return PathResolutionService(scheduler_directory_provider)

    @classmethod
    def _load_from_env(
        cls, config: dict[str, Any], config_manager: Optional[ConfigurationManager] = None
    ) -> None:
        """
        Apply ORB_* environment variable overrides to the raw config dict.

        Only variables that are explicitly set in the environment take effect.
        Precedence: env var > config file > schema default.

        Args:
            config: Configuration dictionary to update in place
            config_manager: Configuration manager for scheduler directories
        """
        if val := os.environ.get("ORB_LOG_LEVEL"):
            config.setdefault("logging", {})["level"] = val
        if val := cls._resolve_console_enabled():
            config.setdefault("logging", {})["console_enabled"] = val.lower() == "true"
        if val := os.environ.get("ORB_DEBUG"):
            config["debug"] = val.lower() == "true"
        if val := os.environ.get("ORB_ENVIRONMENT"):
            config["environment"] = val
        if val := os.environ.get("ORB_REQUEST_TIMEOUT"):
            config["request_timeout"] = int(val)
        if val := os.environ.get("ORB_MAX_MACHINES_PER_REQUEST"):
            config["max_machines_per_request"] = int(val)
        if val := os.environ.get("ORB_CONFIG_FILE"):
            config["config_file"] = val

        cls._process_scheduler_directories(config, config_manager)

    @classmethod
    def _resolve_console_enabled(cls) -> Optional[str]:
        """
        Resolve the console logging enabled setting from environment variables.

        Returns:
            String value of the env var if set, None otherwise
        """
        return os.environ.get("ORB_LOG_CONSOLE_ENABLED")

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
            svc = cls._get_path_resolution_service(config_manager)
            scheduler_dir = svc.resolve_directory("work")
            logs_dir = svc.resolve_directory("log")

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
                existing_base_path = json_strategy.get("base_path", "data")
                if not os.path.isabs(existing_base_path):
                    json_strategy["base_path"] = scheduler_dir
                    get_config_logger().debug("Set JSON storage base_path to %s", scheduler_dir)

                # Update SQL storage strategy database path from scheduler directory.
                # Applied unconditionally — non-SQLite engines ignore the name field.
                sql_strategy = storage.setdefault("sql_strategy", {})
                sql_strategy["name"] = os.path.join(scheduler_dir, "database.db")
                get_config_logger().debug(
                    "Set SQL storage name to %s", os.path.join(scheduler_dir, "database.db")
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
        result = safe_json_loads(value, default=None)
        if result is not None:
            return result

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
        json_str = safe_json_dumps(obj, raise_on_error=True, context="Deep copy serialization")
        return safe_json_loads(json_str, raise_on_error=True, context="Deep copy deserialization")
