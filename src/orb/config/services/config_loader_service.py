"""Centralized configuration loading service.

This service eliminates duplication of configuration loading logic across:
- ConfigurationLoader
- ConfigurationManager
- Various configuration consumers

Architecture:
- Single source of truth for loading configuration
- Consistent precedence: env vars > explicit file > scheduler config > fallback
- Supports multiple configuration sources
"""

import json
import os
from pathlib import Path
from typing import Any, Optional

from orb.config.services.path_resolution_service import PathResolutionService
from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


class ConfigLoaderService:
    """Centralized service for loading configuration from multiple sources."""

    DEFAULT_CONFIG_FILENAME = "default_config.json"

    def __init__(self, path_resolver: PathResolutionService):
        """Initialize configuration loader service.

        Args:
            path_resolver: Path resolution service for finding config files
        """
        self.path_resolver = path_resolver

    def load_config_file(
        self,
        file_type: str,
        filename: str,
        explicit_path: Optional[str] = None,
        required: bool = False,
    ) -> Optional[dict[str, Any]]:
        """Load configuration file with consistent priority.

        Args:
            file_type: Type of file ('config', 'template', 'legacy', etc.)
            filename: Name of the file
            explicit_path: Explicit path provided by user (optional)
            required: Whether the file is required (affects logging level)

        Returns:
            Loaded configuration or None if file not found

        Raises:
            ConfigurationError: If file cannot be loaded or parsed
        """
        logger.debug(
            "Loading config file: type=%s, filename=%s, explicit_path=%s",
            file_type,
            filename,
            explicit_path,
        )

        # Resolve the file path
        resolved_path = self.path_resolver.resolve_file_path(file_type, filename, explicit_path)

        if not resolved_path or not os.path.exists(resolved_path):
            if required:
                logger.error("Required %s configuration file not found: %s", file_type, filename)
            else:
                logger.debug("Optional %s configuration file not found: %s", file_type, filename)
            return None

        # Load the file
        logger.info("Loading %s configuration from: %s", file_type, resolved_path)
        return self._load_from_file(resolved_path)

    def _load_from_file(self, config_path: str) -> Optional[dict[str, Any]]:
        """Load configuration from file.

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
            raise ConfigurationError("Config", f"Invalid JSON in configuration file: {e!s}")
        except Exception as e:
            raise ConfigurationError("Config", f"Failed to load configuration file: {e!s}")

    def load_default_config(self) -> dict[str, Any]:
        """Load default configuration from project config directory.

        Returns:
            Default configuration dictionary
        """
        logger.debug("Loading default configuration")

        try:
            # Use platform_dirs to get the correct config location
            from orb.config.platform_dirs import get_config_location

            config_location = get_config_location()
            default_config_path = config_location / self.DEFAULT_CONFIG_FILENAME

            if default_config_path.exists():
                with open(default_config_path) as f:
                    config_data = json.load(f)
                    logger.info("Loaded default configuration from %s", default_config_path)
                    return config_data
            else:
                logger.warning(f"Default config not found: {default_config_path}")
                return {}
        except Exception as e:
            logger.warning(f"Failed to load default configuration: {e}", exc_info=True)
            return {}

    def merge_configs(self, base: dict[str, Any], update: dict[str, Any]) -> None:
        """Merge update configuration into base configuration.

        Arrays are replaced entirely, not merged element by element.

        Args:
            base: Base configuration to update (modified in place)
            update: Update configuration
        """
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                # Deep merge for dictionaries
                self.merge_configs(base[key], value)
            else:
                # Replace for all other types (including arrays)
                base[key] = value

    def expand_env_vars(self, config: dict[str, Any]) -> dict[str, Any]:
        """Expand environment variables in configuration.

        Args:
            config: Configuration dictionary

        Returns:
            Configuration with environment variables expanded
        """
        from orb.config.utils.env_expansion import expand_config_env_vars

        return expand_config_env_vars(config)

    def convert_value(self, value: str) -> Any:
        """Convert string values to appropriate types.

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


def create_config_loader_service(path_resolver: PathResolutionService) -> ConfigLoaderService:
    """Factory function for creating ConfigLoaderService.

    Args:
        path_resolver: Path resolution service

    Returns:
        ConfigLoaderService instance
    """
    return ConfigLoaderService(path_resolver)
