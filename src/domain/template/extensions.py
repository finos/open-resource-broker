"""Template extension registry for provider-specific extensions."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel


class TemplateExtension(ABC):
    """Abstract base class for template extensions."""

    @abstractmethod
    def to_template_defaults(self) -> Dict[str, Any]:
        """Convert extension to template defaults format."""

    @abstractmethod
    def get_provider_type(self) -> str:
        """Get the provider type this extension supports."""


class TemplateExtensionRegistry:
    """Registry for provider-specific template extensions.

    This registry follows the Open/Closed Principle - it's open for extension
    (new providers can be added) but closed for modification (existing code
    doesn't need to change when new providers are added).
    """

    _extensions: Dict[str, Type[BaseModel]] = {}
    _extension_instances: Dict[str, TemplateExtension] = {}

    @classmethod
    def register_extension(cls, provider_type: str, extension_class: Type[BaseModel]) -> None:
        """Register a provider-specific extension configuration class.

        Args:
            provider_type: The provider type (e.g., 'aws', 'provider1', 'provider2')
            extension_class: The Pydantic model class for the extension configuration
        """
        if not issubclass(extension_class, BaseModel):
            raise ValueError(f"Extension class must be a Pydantic BaseModel, got {extension_class}") from e

        cls._extensions[provider_type] = extension_class

    @classmethod
    def register_extension_instance(
        cls, provider_type: str, extension_instance: TemplateExtension
    ) -> None:
        """Register a provider-specific extension instance.

        Args:
            provider_type: The provider type (e.g., 'aws', 'provider1', 'provider2')
            extension_instance: The extension instance implementing TemplateExtension
        """
        if not isinstance(extension_instance, TemplateExtension):
            raise ValueError(
                f"Extension instance must implement TemplateExtension, got { type(extension_instance)}"
            )

        cls._extension_instances[provider_type] = extension_instance

    @classmethod
    def get_extension_class(cls, provider_type: str) -> Optional[Type[BaseModel]]:
        """Get extension configuration class for a provider.

        Args:
            provider_type: The provider type to get extension class for

        Returns:
            The extension configuration class or None if not registered
        """
        return cls._extensions.get(provider_type)

    @classmethod
    def get_extension_instance(cls, provider_type: str) -> Optional[TemplateExtension]:
        """Get extension instance for a provider.

        Args:
            provider_type: The provider type to get extension instance for

        Returns:
            The extension instance or None if not registered
        """
        return cls._extension_instances.get(provider_type)

    @classmethod
    def has_extension(cls, provider_type: str) -> bool:
        """Check if a provider has registered extensions.

        Args:
            provider_type: The provider type to check

        Returns:
            True if the provider has registered extensions
        """
        return provider_type in cls._extensions or provider_type in cls._extension_instances

    @classmethod
    def get_supported_providers(cls) -> list[str]:
        """Get list of providers with registered extensions.

        Returns:
            List of provider types that have registered extensions
        """
        all_providers = set(cls._extensions.keys()) | set(cls._extension_instances.keys())
        return list(all_providers)

    @classmethod
    def create_extension_config(
        cls, provider_type: str, config_data: Dict[str, Any]
    ) -> Optional[BaseModel]:
        """Create an extension configuration instance for a provider.

        Args:
            provider_type: The provider type
            config_data: The configuration data to create the extension with

        Returns:
            The created extension configuration instance or None if provider not registered
        """
        extension_class = cls.get_extension_class(provider_type)
        if extension_class:
            return extension_class(**config_data)
        return None

    @classmethod
    def get_extension_defaults(
        cls, provider_type: str, config_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get template defaults from provider extension.

        Args:
            provider_type: The provider type
            config_data: Optional configuration data to create extension with

        Returns:
            Dictionary of template defaults from the extension
        """
        # Try extension instance first
        extension_instance = cls.get_extension_instance(provider_type)
        if extension_instance:
            return extension_instance.to_template_defaults()

        # Try creating from extension class
        if config_data:
            extension_config = cls.create_extension_config(provider_type, config_data)
            if extension_config and hasattr(extension_config, "to_template_defaults"):
                return extension_config.to_template_defaults()

        return {}

    @classmethod
    def clear_registry(cls) -> None:
        """Clear all registered extensions (mainly for testing)."""
        cls._extensions.clear()
        cls._extension_instances.clear()


# Convenience functions for common operations
def register_provider_extension(provider_type: str, extension_class: Type[BaseModel]) -> None:
    """Register a provider extension."""
    TemplateExtensionRegistry.register_extension(provider_type, extension_class)


def get_provider_extension_defaults(
    provider_type: str, config_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Get provider extension defaults."""
    return TemplateExtensionRegistry.get_extension_defaults(provider_type, config_data)


def has_provider_extension(provider_type: str) -> bool:
    """Check if provider has extensions."""
    return TemplateExtensionRegistry.has_extension(provider_type)
