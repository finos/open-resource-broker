"""Provider configuration management."""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from config.schemas.provider_strategy_schema import (
        ProviderConfig,
        ProviderInstanceConfig,
    )

from config.schemas.provider_strategy_schema import ProviderMode
from domain.base.exceptions import ConfigurationError

logger = logging.getLogger(__name__)


class ProviderConfigManager:
    """Manages provider-specific configuration."""

    def __init__(self, raw_config: Dict[str, Any]) -> None:
        """Initialize the instance."""
        self._raw_config = raw_config

    def get_storage_strategy(self) -> str:
        """Get storage strategy from configuration."""
        return self._get_nested_value("storage.strategy", "json")

    def get_scheduler_strategy(self) -> str:
        """Get scheduler strategy from configuration."""
        # Look for scheduler.type (matches config file) instead of scheduler.strategy
        return self._get_nested_value("scheduler.type", "default")

    def get_provider_type(self) -> str:
        """Get provider type from configuration."""
        return self._get_nested_value("provider.type", "aws")

    def get_provider_config(self) -> Optional["ProviderConfig"]:
        """Get provider configuration."""
        try:
            from config.schemas.provider_strategy_schema import ProviderConfig

            provider_data = self._raw_config.get("provider", {})
            if not provider_data:
                return None

            return ProviderConfig(**provider_data)
        except Exception as e:
            logger.error("Failed to load provider config: %s", e)
            return None

    def is_provider_strategy_enabled(self) -> bool:
        """Check if provider strategy mode is enabled."""
        provider_mode = self.get_provider_mode()
        return provider_mode == ProviderMode.STRATEGY.value

    def is_multi_provider_mode(self) -> bool:
        """Check if multi-provider mode is enabled."""
        provider_config = self.get_provider_config()
        if not provider_config:
            return False

        return len(getattr(provider_config, "providers", [])) > 1

    def get_provider_mode(self) -> str:
        """Get current provider mode."""
        return self._get_nested_value("provider.mode", ProviderMode.LEGACY.value)

    def get_active_provider_names(self) -> List[str]:
        """Get list of active provider names."""
        provider_config = self.get_provider_config()
        if not provider_config:
            return []

        providers = getattr(provider_config, "providers", [])
        return [provider.name for provider in providers if getattr(provider, "enabled", True)]

    def get_provider_instance_config(
        self, provider_name: str
    ) -> Optional["ProviderInstanceConfig"]:
        """Get configuration for a specific provider instance."""
        provider_config = self.get_provider_config()
        if not provider_config:
            return None

        providers = getattr(provider_config, "providers", [])
        for provider in providers:
            if provider.name == provider_name:
                return provider

        return None

    def save_provider_config(self, provider_config: "ProviderConfig") -> None:
        """Save provider configuration."""
        try:
            # Convert provider config to dict
            provider_dict = (
                provider_config.model_dump()
                if hasattr(provider_config, "model_dump")
                else provider_config.__dict__
            )

            # Update the raw config
            self._raw_config["provider"] = provider_dict

            logger.info("Provider configuration saved")
        except Exception as e:
            logger.error("Failed to save provider config: %s", e)
            raise ConfigurationError(f"Failed to save provider configuration: {e}")

    def _get_nested_value(self, key: str, default: Any = None) -> Any:
        """Get nested configuration value using dot notation."""
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
