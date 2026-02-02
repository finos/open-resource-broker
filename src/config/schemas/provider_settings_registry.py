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
    def register_provider_settings(cls, provider_type: str, settings_class: Type[BaseSettings]) -> None:
        """Register a provider-specific settings class."""
        cls._settings_classes[provider_type] = settings_class
    
    @classmethod
    def get_registered_provider_types(cls) -> list[str]:
        """Get list of registered provider types."""
        return list(cls._settings_classes.keys())
    
    @classmethod
    def get_settings_class(cls, provider_type: str) -> Type[BaseSettings]:
        return cls._settings_classes.get(provider_type, BaseSettings)
    
    @classmethod
    def create_settings(cls, provider_type: str, config_dict: dict) -> BaseSettings:
        settings_class = cls.get_settings_class(provider_type)
        
        # Create settings instance - env vars automatically loaded
        settings = settings_class()
        
        # Get default values to compare against
        default_settings = settings_class.__pydantic_fields__
        
        # Only override with config_dict if field wasn't set by environment variable
        for key, value in config_dict.items():
            if hasattr(settings, key):
                # Get the current value and default value
                current_value = getattr(settings, key)
                default_value = default_settings[key].default if key in default_settings else None
                
                # Only use config_dict value if current value is still the default
                # (meaning no env var was set)
                if current_value == default_value:
                    setattr(settings, key, value)
        
        return settings