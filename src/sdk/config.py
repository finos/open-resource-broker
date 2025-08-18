"""
SDK configuration management following existing configuration patterns.

Integrates with the existing configuration system while providing
SDK-specific configuration options and validation.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .exceptions import ConfigurationError


@dataclass
class SDKConfig:
    """
    SDK configuration with environment variable support.

    Follows the same patterns as existing configuration classes
    for consistency and integration with the existing config system.
    """

    # Provider configuration
    provider: str = "aws"
    region: Optional[str] = None
    profile: Optional[str] = None

    # Operation configuration
    timeout: int = 300
    retry_attempts: int = 3

    # Logging configuration
    log_level: str = "INFO"

    # Custom configuration for advanced usage
    custom_config: Dict[str, Any] = field(default_factory=dict)

    # Internal configuration path
    config_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "SDKConfig":
        """
        Create configuration from environment variables.

        Uses the same environment variable patterns as the existing system.
        """
        return cls(
            provider=os.getenv("OHFP_PROVIDER", "aws"),
            region=os.getenv("OHFP_REGION"),
            profile=os.getenv("OHFP_PROFILE"),
            timeout=int(os.getenv("OHFP_TIMEOUT", "300")),
            retry_attempts=int(os.getenv("OHFP_RETRY_ATTEMPTS", "3")),
            log_level=os.getenv("OHFP_LOG_LEVEL", "INFO"),
            config_path=os.getenv("OHFP_CONFIG_PATH"),
        )

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "SDKConfig":
        """Create configuration from dictionary."""
        # Extract known fields
        known_fields = {field.name for field in cls.__dataclass_fields__.values()}

        sdk_config = {}
        custom_config = {}

        for key, value in config.items():
            if key in known_fields:
                sdk_config[key] = value
            else:
                custom_config[key] = value

        if custom_config:
            sdk_config["custom_config"] = custom_config

        return cls(**sdk_config)

    @classmethod
    def from_file(cls, path: str) -> "SDKConfig":
        """
        Create configuration from file (JSON or YAML).

        Follows the same file loading patterns as existing config system.
        """
        file_path = Path(path)

        if not file_path.exists():
            raise ConfigurationError(f"Configuration file not found: {path}") from e

        try:
            with open(file_path, "r") as f:
                if file_path.suffix.lower() in [".yml", ".yaml"]:
                    try:
                        import yaml

                        data = yaml.safe_load(f)
                    except ImportError:
                        raise ConfigurationError("YAML support requires PyYAML: pip install PyYAML") from e
                else:
                    data = json.load(f)

            config = cls.from_dict(data)
            config.config_path = str(file_path)
            return config

        except Exception as e:
            raise ConfigurationError(f"Failed to load configuration from {path}: {str(e)}") from e

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        result = {
            "provider": self.provider,
            "region": self.region,
            "profile": self.profile,
            "timeout": self.timeout,
            "retry_attempts": self.retry_attempts,
            "log_level": self.log_level,
        }

        # Add custom configuration
        result.update(self.custom_config)

        # Remove None values
        return {k: v for k, v in result.items() if v is not None}

    def validate(self) -> None:
        """
        Validate configuration values.

        Follows the same validation patterns as existing config classes.
        """
        if not self.provider:
            raise ConfigurationError("Provider is required") from e

        if self.timeout <= 0:
            raise ConfigurationError("Timeout must be positive") from e

        if self.retry_attempts < 0:
            raise ConfigurationError("Retry attempts cannot be negative") from e

        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level.upper() not in valid_log_levels:
            raise ConfigurationError(
                f"Invalid log level: {self.log_level}. "
                f"Valid levels: {', '.join(valid_log_levels)}"
            )
