"""Registry for provider-specific BaseSettings classes."""

from typing import Type

from pydantic_settings import BaseSettings


class ProviderSettingsRegistry:
    """Registry for provider-specific BaseSettings classes."""

    _settings_classes = {
        # Provider settings classes will be registered dynamically
        # "aws": AWSProviderSettings,  # Will be added when AWS provider is registered
    }

    @classmethod
    def register_provider_settings(
        cls, provider_type: str, settings_class: Type[BaseSettings]
    ) -> None:
        """Register a provider-specific settings class."""
        cls._settings_classes[provider_type] = settings_class

    @classmethod
    def get_registered_provider_types(cls) -> list[str]:
        """Get list of registered provider types."""
        return list(cls._settings_classes.keys())

    @classmethod
    def get_settings_class(cls, provider_type: str) -> Type[BaseSettings]:
        return cls._settings_classes.get(provider_type, BaseSettings)
