"""
SDK configuration management following existing configuration patterns.

Integrates with the existing configuration system while providing
SDK-specific configuration options and validation.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .exceptions import ConfigurationError


@dataclass
class SDKConfig:
    """
    SDK configuration with environment variable support.

    Follows the same patterns as existing configuration classes
    for consistency and integration with the existing config system.

    Deprecation notice
    ------------------
    The ``region`` and ``profile`` fields were removed in v1.7 and are now
    available as backward-compatibility shims that forward to
    ``provider_config``.  They will be removed in v2.0.  Use
    ``provider_config={"region": ..., "profile": ...}`` instead.
    """

    # Provider configuration
    provider: str = "aws"
    provider_type: Optional[str] = None
    provider_name: Optional[str] = None
    provider_config: dict[str, Any] = field(default_factory=dict)
    scheduler: Optional[str] = None

    # Operation configuration
    timeout: int = 300
    retry_attempts: int = 3

    # Logging configuration
    log_level: str = "INFO"

    # Custom configuration for advanced usage
    custom_config: dict[str, Any] = field(default_factory=dict)

    # Internal configuration path
    config_path: Optional[str] = None

    # ------------------------------------------------------------------
    # Backward-compatibility shims — deprecated in v1.8, removed in v2.0
    # ------------------------------------------------------------------

    @property  # type: ignore[misc]
    def region(self) -> Optional[str]:
        """Deprecated: read ``provider_config['region']`` instead."""
        import warnings

        warnings.warn(
            "SDKConfig.region is deprecated and will be removed in the next major release; "
            "read provider_config['region'] instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.provider_config.get("region")

    @region.setter  # type: ignore[misc]
    def region(self, value: Optional[str]) -> None:
        """Deprecated: write ``provider_config['region']`` instead."""
        import warnings

        warnings.warn(
            "SDKConfig.region setter is deprecated and will be removed in the next major release; "
            "write provider_config['region'] instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if value is None:
            self.provider_config.pop("region", None)
        else:
            self.provider_config["region"] = value

    @property  # type: ignore[misc]
    def profile(self) -> Optional[str]:
        """Deprecated: read ``provider_config['profile']`` instead."""
        import warnings

        warnings.warn(
            "SDKConfig.profile is deprecated and will be removed in the next major release; "
            "read provider_config['profile'] instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.provider_config.get("profile")

    @profile.setter  # type: ignore[misc]
    def profile(self, value: Optional[str]) -> None:
        """Deprecated: write ``provider_config['profile']`` instead."""
        import warnings

        warnings.warn(
            "SDKConfig.profile setter is deprecated and will be removed in the next major release; "
            "write provider_config['profile'] instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if value is None:
            self.provider_config.pop("profile", None)
        else:
            self.provider_config["profile"] = value

    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "SDKConfig":
        """
        Create configuration from environment variables.

        Uses the same environment variable patterns as the existing system.

        Deprecated env vars ``ORB_REGION`` and ``ORB_PROFILE`` are still read
        and folded into ``provider_config`` with a deprecation warning.
        """
        provider_config: dict[str, Any] = {}

        legacy_region = os.getenv("ORB_REGION")
        legacy_profile = os.getenv("ORB_PROFILE")
        if legacy_region is not None or legacy_profile is not None:
            import warnings

            warnings.warn(
                "ORB_REGION and ORB_PROFILE environment variables are deprecated and will be "
                "removed in the next major release; pass provider_config={'region': ..., "
                "'profile': ...} programmatically or via a config file instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            if legacy_region is not None:
                provider_config["region"] = legacy_region
            if legacy_profile is not None:
                provider_config["profile"] = legacy_profile

        return cls(
            provider=os.getenv("ORB_PROVIDER", "aws"),
            provider_type=os.getenv("ORB_PROVIDER_TYPE"),
            provider_name=os.getenv("ORB_PROVIDER_NAME"),
            provider_config=provider_config,
            timeout=int(os.getenv("ORB_TIMEOUT", "300")),
            retry_attempts=int(os.getenv("ORB_RETRY_ATTEMPTS", "3")),
            log_level=os.getenv("ORB_LOG_LEVEL", "INFO"),
            config_path=os.getenv("ORB_CONFIG_FILE"),
        )

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "SDKConfig":
        """Create configuration from dictionary.

        Deprecated top-level keys ``region`` and ``profile`` are still accepted
        and automatically folded into ``provider_config`` with a deprecation
        warning.  They will be removed in v2.0.
        """
        # Backward-compat: legacy top-level ``region`` / ``profile`` keys are
        # deprecated.  Fold them into ``provider_config`` and warn.
        legacy_region = config.get("region")
        legacy_profile = config.get("profile")
        if legacy_region is not None or legacy_profile is not None:
            import warnings

            warnings.warn(
                "SDKConfig.from_dict() with top-level 'region' or 'profile' keys is deprecated "
                "and will be removed in the next major release; move them into "
                "provider_config={'region': ..., 'profile': ...}.",
                DeprecationWarning,
                stacklevel=2,
            )
            # Remove legacy keys so they do not land in custom_config.
            config = {k: v for k, v in config.items() if k not in ("region", "profile")}
            pc: dict[str, Any] = dict(config.get("provider_config") or {})
            if legacy_region is not None:
                pc.setdefault("region", legacy_region)
            if legacy_profile is not None:
                pc.setdefault("profile", legacy_profile)
            config = {**config, "provider_config": pc}

        # Extract known fields.
        known_fields = {field.name for field in cls.__dataclass_fields__.values()}

        sdk_config = {}
        custom_config = {}

        for key, value in config.items():
            if key in known_fields:
                # ``scheduler`` is a plain string override in SDKConfig (e.g. "default"
                # or "hostfactory").  When loading from an ORB config.json the top-level
                # ``scheduler`` key is the full scheduler sub-config dict
                # ({"type": "hostfactory", "config_root": "..."}).  Ingesting that dict
                # as the string override causes ConfigurationManager.override_scheduler_strategy
                # to store a dict, which then propagates as the scheduler_type into the
                # registry lookup and fails with "unhashable type: 'dict'".
                if key == "scheduler" and isinstance(value, dict):
                    # The ORB config scheduler object is not an SDK string override;
                    # skip it so the scheduler type is resolved from the config file.
                    custom_config[key] = value
                else:
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
            raise ConfigurationError(f"Configuration file not found: {path}")

        try:
            with open(file_path) as f:
                if file_path.suffix.lower() in [".yml", ".yaml"]:
                    try:
                        import yaml

                        data = yaml.safe_load(f)
                    except ImportError:
                        raise ConfigurationError("YAML support requires PyYAML: pip install PyYAML")
                else:
                    data = json.load(f)

            config = cls.from_dict(data)
            config.config_path = str(file_path)
            return config

        except Exception as e:
            raise ConfigurationError(f"Failed to load configuration from {path}: {e!s}")

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary."""
        result: dict[str, Any] = {
            "provider": self.provider,
            "provider_type": self.provider_type,
            "provider_name": self.provider_name,
            "timeout": self.timeout,
            "retry_attempts": self.retry_attempts,
            "log_level": self.log_level,
        }

        if self.provider_config:
            result["provider_config"] = self.provider_config

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
            raise ConfigurationError("Provider is required")

        if self.timeout <= 0:
            raise ConfigurationError("Timeout must be positive")

        if self.retry_attempts < 0:
            raise ConfigurationError("Retry attempts cannot be negative")

        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level.upper() not in valid_log_levels:
            raise ConfigurationError(
                f"Invalid log level: {self.log_level}. Valid levels: {', '.join(valid_log_levels)}"
            )
