"""Configuration type conversion utilities."""

import logging
from typing import Any, Dict, List, Optional, Type, TypeVar

from domain.base.exceptions import ConfigurationError

T = TypeVar("T")
logger = logging.getLogger(__name__)


class ConfigTypeConverter:
    """Handles type conversion for configuration values."""

    def __init__(self, raw_config: Dict[str, Any]) -> None:
        """Initialize the instance."""
        self._raw_config = raw_config

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key with dot notation support."""
        keys = key.split(".")
        value = self._raw_config

        try:
            for k in keys:
                if isinstance(value, dict):
                    value = value[k]
                else:
                    return default
            return value
        except (KeyError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get boolean configuration value."""
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)

    def get_int(self, key: str, default: int = 0) -> int:
        """Get integer configuration value."""
        value = self.get(key, default)
        try:
            return int(value)
        except (ValueError, TypeError):
            logger.warning("Could not convert config value '%s' to int: %s", key, value)
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Get float configuration value."""
        value = self.get(key, default)
        try:
            return float(value)
        except (ValueError, TypeError):
            logger.warning("Could not convert config value '%s' to float: %s", key, value)
            return default

    def get_str(self, key: str, default: str = "") -> str:
        """Get string configuration value."""
        value = self.get(key, default)
        return str(value) if value is not None else default

    def get_list(self, key: str, default: Optional[List[Any]] = None) -> List[Any]:
        """Get list configuration value."""
        if default is None:
            default = []

        value = self.get(key, default)
        if isinstance(value, list):
            return value
        elif isinstance(value, str):
            # Try to parse comma-separated values
            return [item.strip() for item in value.split(",") if item.strip()]
        else:
            return default

    def get_dict(self, key: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get dictionary configuration value."""
        if default is None:
            default = {}

        value = self.get(key, default)
        return value if isinstance(value, dict) else default

    def get_typed(self, config_class: Type[T]) -> T:
        """Create typed configuration object from raw config."""
        try:
            class_name = config_class.__name__

            # Special handling for AppConfig - it uses the entire raw config
            if class_name == "AppConfig":
                return config_class(**self._raw_config)

            # Special handling for AWSProviderConfig - resolve from provider strategy
            if class_name == "AWSProviderConfig":
                return self._get_aws_provider_config(config_class)

            # Other config classes use their respective sections
            section_name = class_name.replace("Config", "").lower()
            config_data = self.get_dict(section_name, {})

            # Create instance with validation
            return config_class(**config_data)
        except Exception as e:
            logger.error("Failed to create typed config for %s: %s", config_class.__name__, e)
            raise ConfigurationError(
                f"Invalid configuration for {config_class.__name__}: {e}"
            ) from e

    def _get_aws_provider_config(self, config_class: Type[T]) -> T:
        """Get AWS provider configuration from provider strategy system."""
        try:
            # Get provider configuration
            provider_config = self.get_dict("provider", {})

            # Get from provider instances (provider strategy)
            providers = provider_config.get("providers", [])
            active_provider = provider_config.get("active_provider")

            # Find the active AWS provider or first enabled AWS provider
            aws_provider_config = None

            # First, try to find the active provider if specified
            if active_provider:
                for provider in providers:
                    if (
                        provider.get("name") == active_provider
                        and provider.get("type") == "aws"
                        and provider.get("enabled", True)
                    ):
                        aws_provider_config = provider.get("config", {})
                        logger.debug("Using AWS config from active provider: %s", active_provider)
                        break

            # If no active provider found, use first enabled AWS provider
            if not aws_provider_config:
                for provider in providers:
                    if provider.get("type") == "aws" and provider.get("enabled", True):
                        aws_provider_config = provider.get("config", {})
                        logger.debug("Using AWS config from provider: %s", provider.get("name"))
                        break

            if aws_provider_config:
                return config_class(**aws_provider_config)
            else:
                logger.warning("No enabled AWS provider found in configuration")
                return config_class()

        except Exception as e:
            logger.error("Failed to resolve AWS provider config: %s", e)
            raise

    def set(self, key: str, value: Any) -> None:
        """Set configuration value by key with dot notation support."""
        keys = key.split(".")
        config = self._raw_config

        # Navigate to the parent of the target key
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        # Set the final value
        config[keys[-1]] = value

    def update(self, updates: Dict[str, Any]) -> None:
        """Update configuration with new values."""

        def deep_update(base_dict: Dict[str, Any], update_dict: Dict[str, Any]) -> None:
            """Recursively update nested dictionary values."""
            for key, value in update_dict.items():
                if (
                    key in base_dict
                    and isinstance(base_dict[key], dict)
                    and isinstance(value, dict)
                ):
                    deep_update(base_dict[key], value)
                else:
                    base_dict[key] = value

        deep_update(self._raw_config, updates)
