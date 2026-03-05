"""Unified configuration loading utilities - eliminates duplication across config modules.

This module provides centralized configuration loading logic to replace duplicated
code in ConfigurationLoader and ConfigLoaderService.
"""

import json
import os
from pathlib import Path
from typing import Any, Optional

from domain.base.exceptions import ConfigurationError
from infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


class ConfigFileLoader:
    """Centralized configuration file loading with consistent error handling."""

    @staticmethod
    def load_json_file(file_path: str) -> Optional[dict[str, Any]]:
        """Load JSON configuration file with standardized error handling.

        Args:
            file_path: Path to JSON configuration file

        Returns:
            Loaded configuration dictionary or None if file not found

        Raises:
            ConfigurationError: If file exists but cannot be parsed
        """
        try:
            path = Path(file_path)
            if not path.exists():
                logger.debug("Configuration file not found: %s", file_path)
                return None

            with path.open() as f:
                config_data = json.load(f)
                logger.debug("Loaded configuration from: %s", file_path)
                return config_data

        except json.JSONDecodeError as e:
            raise ConfigurationError(f"Invalid JSON in configuration file {file_path}: {e!s}")
        except PermissionError as e:
            raise ConfigurationError(
                f"Permission denied reading configuration file {file_path}: {e!s}"
            )
        except Exception as e:
            raise ConfigurationError(f"Failed to load configuration file {file_path}: {e!s}")

    @staticmethod
    def merge_configs(base: dict[str, Any], update: dict[str, Any]) -> None:
        """Deep merge update configuration into base configuration.

        Arrays are replaced entirely, not merged element by element.
        Dictionaries are merged recursively.

        Args:
            base: Base configuration to update (modified in place)
            update: Update configuration to merge in
        """
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                # Deep merge for dictionaries
                ConfigFileLoader.merge_configs(base[key], value)
            else:
                # Replace for all other types (including arrays)
                base[key] = value

    @staticmethod
    def convert_string_value(value: str) -> Any:
        """Convert string values to appropriate Python types.

        Attempts conversion in order: boolean, integer, float, JSON, string.

        Args:
            value: String value to convert

        Returns:
            Converted value with appropriate type
        """
        # Try to convert to boolean
        if value.lower() in ("true", "false"):
            return value.lower() == "true"

        # Try to convert to integer
        try:
            return int(value)
        except ValueError:
            pass

        # Try to convert to float
        try:
            return float(value)
        except ValueError:
            pass

        # Try to convert to JSON
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass

        # Return as string if no conversion possible
        return value

    @staticmethod
    def validate_config_path(config_path: str, required: bool = False) -> bool:
        """Validate that configuration path exists and is readable.

        Args:
            config_path: Path to validate
            required: Whether the file is required

        Returns:
            True if path exists and is readable, False otherwise

        Raises:
            ConfigurationError: If required file is missing or not readable
        """
        path = Path(config_path)

        if not path.exists():
            if required:
                raise ConfigurationError(f"Required configuration file not found: {config_path}")
            return False

        if not os.access(config_path, os.R_OK):
            if required:
                raise ConfigurationError(f"Configuration file not readable: {config_path}")
            return False

        return True
