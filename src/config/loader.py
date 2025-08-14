"""Configuration loading utilities."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, TypeVar

from src.config.schemas import AppConfig, validate_config
from src.domain.base.exceptions import ConfigurationError

T = TypeVar("T")


# Use lazy import to avoid circular dependency
def _get_logger():
    """Lazy import of logger to avoid circular dependency."""
    from src.infrastructure.logging.logger import get_logger

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

    # Host Factory environment variables
    HF_ENV_VARS = {
        "HF_PROVIDER_WORKDIR": "workdir",
        "HF_PROVIDER_LOGDIR": "logs",
        "HF_PROVIDER_CONFDIR": "config",
        "HF_PROVIDER_EVENTSDIR": "events",
        "HF_PROVIDER_SNAPSHOTSDIR": "snapshots",
        "HF_PROVIDER_NAME": "provider_name",
        "HF_TEMPLATE_AMI_RESOLUTION_ENABLED": "template.ami_resolution.enabled",
        "HF_TEMPLATE_AMI_RESOLUTION_FALLBACK_ON_FAILURE": "template.ami_resolution.fallback_on_failure",
        "HF_TEMPLATE_AMI_RESOLUTION_CACHE_FILE": "template.ami_resolution.persistent_cache_file",
    }

    # Default configuration file name
    DEFAULT_CONFIG_FILENAME = "default_config.json"

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Load configuration from multiple sources with proper precedence.

        Precedence order (highest to lowest):
        1. Environment variables (highest precedence)
        2. Explicit config file (if config_path provided)
        3. HF_PROVIDER_CONFDIR/config.json (provider-specific)
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

        # Load main config.json with proper precedence (HF_PROVIDER_CONFDIR first,
        # then config/)
        main_config = cls._load_config_file("conf", "config.json", required=False)
        if main_config:
            # Handle schema differences: main config may have 'providers' at root level
            # while default config has 'provider.providers'
            if 'providers' in main_config and 'provider' not in main_config:
                # Normalize main config to match default config structure
                normalized_main_config = main_config.copy()
                normalized_main_config['provider'] = {
                    'providers': main_config['providers']
                }
                # Remove the root-level 'providers' to avoid duplication
                del normalized_main_config['providers']
                cls._merge_config(config, normalized_main_config)
            else:
                cls._merge_config(config, main_config)
            get_config_logger().info("Loaded main configuration")

        # Load explicit configuration file if provided (higher precedence)
        if config_path:
            get_config_logger().debug(f"Loading user configuration from: {config_path}")

            # Extract filename from path for file resolution
            filename = os.path.basename(config_path) if config_path else "config.json"

            file_config = cls._load_config_file(
                "conf", filename, explicit_path=config_path, required=False
            )
            if file_config:
                cls._merge_config(config, file_config)
                get_config_logger().info("Loaded user configuration")
            else:
                get_config_logger().warning(f"User configuration file not found: {config_path}")

        # Override with environment variables (highest precedence)
        cls._load_from_env(config)

        # Expand environment variables in the final configuration
        from src.config.utils.env_expansion import expand_config_env_vars

        config = expand_config_env_vars(config)

        return config

    @classmethod
    def _load_default_config(cls) -> Dict[str, Any]:
        """
        Load default configuration from file.

        First tries to load from HF_PROVIDER_CONFDIR, then falls back to local config.

        Returns:
            Default configuration dictionary
        """
        get_config_logger().debug("Loading default configuration")

        # Use file loading method
        config = cls._load_config_file("conf", cls.DEFAULT_CONFIG_FILENAME, required=False)

        if config:
            get_config_logger().info("Loaded default configuration successfully")
            return config
        else:
            get_config_logger().warning(
                "Failed to load default configuration from any location. "
                "Using empty configuration."
            )
            return {}

    @classmethod
    def create_app_config(cls, config: Dict[str, Any]) -> AppConfig:
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
            raise ConfigurationError("App", f"Configuration validation failed: {str(e)}")
        except KeyError as e:
            raise ConfigurationError("App", f"Missing required configuration: {str(e)}")
        except Exception as e:
            raise ConfigurationError("App", f"Failed to create typed configuration: {str(e)}")

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
                get_config_logger().warning(f"Configuration file not found: {config_path}")
                return None

            with path.open() as f:
                return json.load(f)

        except json.JSONDecodeError as e:
            raise ConfigurationError(f"Invalid JSON in configuration file: {str(e)}")
        except Exception as e:
            raise ConfigurationError(f"Failed to load configuration file: {str(e)}")

    @classmethod
    def _load_config_file(
        cls,
        file_type: str,
        filename: str,
        explicit_path: Optional[str] = None,
        required: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Centralized method for loading any configuration file with consistent priority:
        1. Explicit path (if provided and contains directory)
        2. HF_PROVIDER_*DIR + filename (if file exists)
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
            f"Loading config file: type={file_type}, filename={filename}, explicit_path={explicit_path}"
        )

        # Resolve the file path using centralized logic
        # In practice, this would be refactored to use a static method or utility
        resolved_path = cls._resolve_file_path(file_type, filename, explicit_path)

        if resolved_path:
            get_config_logger().info(f"Loading {file_type} configuration from: {resolved_path}")
            return cls._load_from_file(resolved_path)
        else:
            if required:
                get_config_logger().error(
                    f"Required {file_type} configuration file not found: {filename}"
                )
            else:
                get_config_logger().debug(
                    f"Optional {file_type} configuration file not found: {filename}"
                )
            return None

    @classmethod
    def _resolve_file_path(
        cls, file_type: str, filename: str, explicit_path: Optional[str] = None
    ) -> Optional[str]:
        """
        Resolve file path using centralized logic (static version of ConfigurationManager.resolve_file).

        Args:
            file_type: Type of file ('conf', 'template', 'legacy', 'log', 'work', 'events', 'snapshots')
            filename: Name of the file
            explicit_path: Explicit path provided by user (optional)

        Returns:
            Resolved file path or None if not found
        """
        get_config_logger().debug(
            f"Resolving file path: type={file_type}, filename={filename}, explicit_path={explicit_path}"
        )

        # 1. If explicit path provided and contains directory, use it directly
        if explicit_path and os.path.dirname(explicit_path):
            get_config_logger().debug(f"Using explicit path with directory: {explicit_path}")
            return explicit_path if os.path.exists(explicit_path) else None

        # If explicit_path is just a filename, use it as the filename
        if explicit_path and not os.path.dirname(explicit_path):
            filename = explicit_path
            get_config_logger().debug(f"Using explicit filename: {filename}")

        # 2. Try environment variable directory + filename
        env_dir = None
        env_var_name = None

        if file_type in ["conf", "template", "legacy"]:
            env_dir = os.environ.get("HF_PROVIDER_CONFDIR")
            env_var_name = "HF_PROVIDER_CONFDIR"
        elif file_type == "log":
            env_dir = os.environ.get("HF_PROVIDER_LOGDIR")
            env_var_name = "HF_PROVIDER_LOGDIR"
        elif file_type == "work":
            env_dir = os.environ.get("HF_PROVIDER_WORKDIR")
            env_var_name = "HF_PROVIDER_WORKDIR"
        elif file_type == "events":
            env_dir = os.environ.get("HF_PROVIDER_EVENTSDIR")
            env_var_name = "HF_PROVIDER_EVENTSDIR"
        elif file_type == "snapshots":
            env_dir = os.environ.get("HF_PROVIDER_SNAPSHOTSDIR")
            env_var_name = "HF_PROVIDER_SNAPSHOTSDIR"

        if env_dir:
            env_path = os.path.join(env_dir, filename)
            if os.path.exists(env_path):
                get_config_logger().debug(f"Found file using {env_var_name}: {env_path}")
                return env_path
            else:
                get_config_logger().debug(f"File not found in {env_var_name} directory: {env_path}")

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

        # Build path relative to project root
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        fallback_path = os.path.join(project_root, default_dir, filename)

        # Always return the fallback path, even if file doesn't exist
        # This allows the caller to decide whether to create the file or handle
        # the missing file
        get_config_logger().debug(f"Using fallback path: {fallback_path}")
        return fallback_path

    @classmethod
    def _load_from_env(cls, config: Dict[str, Any]) -> None:
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

        # Process Host Factory environment variables
        cls._process_hf_env_vars(config)

        # HF_ prefixed environment variables (legacy support)
        env_prefix = "HF_"
        for key, value in os.environ.items():
            if key.startswith(env_prefix) and key not in cls.HF_ENV_VARS:
                config_key = key[len(env_prefix) :].upper()

                # Handle nested configuration using double underscore
                if "__" in config_key:
                    parts = config_key.split("__")
                    current = config
                    for part in parts[:-1]:
                        current = current.setdefault(part, {})
                    current[parts[-1]] = cls._convert_value(value)
                else:
                    # Add to root config
                    config[config_key] = cls._convert_value(value)

        get_config_logger().debug("Loaded configuration from environment variables")

    @classmethod
    def _process_hf_env_vars(cls, config: Dict[str, Any]) -> None:
        """
        Process Host Factory environment variables.

        Args:
            config: Configuration dictionary to update
        """
        # Get environment variables
        workdir = os.environ.get("HF_PROVIDER_WORKDIR")
        logdir = os.environ.get("HF_PROVIDER_LOGDIR")
        confdir = os.environ.get("HF_PROVIDER_CONFDIR")
        eventsdir = os.environ.get("HF_PROVIDER_EVENTSDIR")
        snapshotsdir = os.environ.get("HF_PROVIDER_SNAPSHOTSDIR")

        # Set up logging path based on HF_PROVIDER_LOGDIR
        if logdir:
            config.setdefault("logging", {})["file_path"] = os.path.join(logdir, "app.log")
            get_config_logger().debug(f"Set logging file_path to {os.path.join(logdir, 'app.log')}")
        elif workdir:
            log_dir = os.path.join(workdir, "logs")
            config.setdefault("logging", {})["file_path"] = os.path.join(log_dir, "app.log")
            get_config_logger().debug(
                f"Set logging file_path to {os.path.join(log_dir, 'app.log')}"
            )

        # Set up storage paths based on workdir
        if workdir:
            # Update JSON storage strategy
            storage = config.setdefault("storage", {})
            json_strategy = storage.setdefault("json_strategy", {})
            json_strategy["base_path"] = workdir
            get_config_logger().debug(f"Set JSON storage base_path to {workdir}")

            # Update SQL storage strategy if using SQLite
            sql_strategy = storage.setdefault("sql_strategy", {})
            if sql_strategy.get("type", "sqlite") == "sqlite":
                # Always use workdir for SQLite, regardless of host value
                sql_strategy["name"] = os.path.join(workdir, "database.db")
                get_config_logger().debug(
                    f"Set SQLite database path to {os.path.join(workdir, 'database.db')}"
                )

        # Set up config paths based on HF_PROVIDER_CONFDIR
        if confdir:
            # Template paths are now handled by centralized file resolution
            # No need to override them here since the template loading will use
            # resolve_file()
            get_config_logger().debug(
                f"HF_PROVIDER_CONFDIR set to: {confdir} (template paths will be resolved dynamically)"
            )

        # Set up events paths based on HF_PROVIDER_EVENTSDIR
        if eventsdir:
            events_config = config.setdefault("events", {})
            events_config["store_path"] = eventsdir
            events_config["default_events_path"] = eventsdir
            get_config_logger().debug(f"Set events store_path to {eventsdir}")
        elif workdir:
            events_dir = os.path.join(workdir, "events")
            events_config = config.setdefault("events", {})
            events_config["default_events_path"] = events_dir
            get_config_logger().debug(f"Set events default_events_path to {events_dir}")

        # Set up snapshots paths based on HF_PROVIDER_SNAPSHOTSDIR
        if snapshotsdir:
            events_config = config.setdefault("events", {})
            events_config["snapshot_store_path"] = snapshotsdir
            events_config["default_snapshots_path"] = snapshotsdir
            get_config_logger().debug(f"Set snapshots snapshot_store_path to {snapshotsdir}")
        elif workdir:
            snapshots_dir = os.path.join(workdir, "snapshots")
            events_config = config.setdefault("events", {})
            events_config["default_snapshots_path"] = snapshots_dir
            get_config_logger().debug(f"Set snapshots default_snapshots_path to {snapshots_dir}")

        # Process AMI resolution environment variables
        ami_resolution_enabled = os.environ.get("HF_TEMPLATE_AMI_RESOLUTION_ENABLED")
        ami_resolution_fallback = os.environ.get("HF_TEMPLATE_AMI_RESOLUTION_FALLBACK_ON_FAILURE")
        ami_resolution_cache_file = os.environ.get("HF_TEMPLATE_AMI_RESOLUTION_CACHE_FILE")

        if any([ami_resolution_enabled, ami_resolution_fallback, ami_resolution_cache_file]):
            template_config = config.setdefault("template", {})
            ami_resolution_config = template_config.setdefault("ami_resolution", {})

            if ami_resolution_enabled is not None:
                ami_resolution_config["enabled"] = cls._convert_value(ami_resolution_enabled)
                get_config_logger().debug(
                    f"Set ami_resolution.enabled to {ami_resolution_config['enabled']}"
                )

            if ami_resolution_fallback is not None:
                ami_resolution_config["fallback_on_failure"] = cls._convert_value(
                    ami_resolution_fallback
                )
                get_config_logger().debug(
                    f"Set ami_resolution.fallback_on_failure to {ami_resolution_config['fallback_on_failure']}"
                )

            if ami_resolution_cache_file is not None:
                ami_resolution_config["persistent_cache_file"] = ami_resolution_cache_file
                get_config_logger().debug(
                    f"Set ami_resolution.persistent_cache_file to {ami_resolution_config['persistent_cache_file']}"
                )

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
    def _merge_config(cls, base: Dict[str, Any], update: Dict[str, Any]) -> None:
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
    def _deep_copy(cls, obj: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a deep copy of a dictionary.

        Args:
            obj: Dictionary to copy

        Returns:
            Deep copy of dictionary
        """
        return json.loads(json.dumps(obj))
