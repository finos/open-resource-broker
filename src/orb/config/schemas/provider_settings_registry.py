"""Registry for provider-specific config model classes."""

from typing import Type

from orb.infrastructure.interfaces.provider import BaseProviderConfig


class ProviderSettingsRegistry:
    """Registry for provider-specific config model classes."""

    _settings_classes: dict[str, type[BaseProviderConfig]] = {
        # Provider settings classes will be registered dynamically
        # "aws": AWSProviderSettings,  # Will be added when AWS provider is registered
    }

    @classmethod
    def register_provider_settings(
        cls, provider_type: str, settings_class: Type[BaseProviderConfig]
    ) -> None:
        """Register a provider-specific config model class."""
        cls._settings_classes[provider_type] = settings_class

    @classmethod
    def get_registered_provider_types(cls) -> list[str]:
        """Get list of registered provider types."""
        return list(cls._settings_classes.keys())

    @classmethod
    def get_settings_class(cls, provider_type: str) -> Type[BaseProviderConfig]:
        return cls._settings_classes.get(provider_type, BaseProviderConfig)
