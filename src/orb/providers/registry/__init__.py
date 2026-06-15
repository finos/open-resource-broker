"""Provider registry package - re-exports all public symbols for backward compatibility."""

from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry
from orb.providers.registry.provider_registry import ProviderRegistry, get_provider_registry
from orb.providers.registry.types import (
    ProviderFactoryInterface,
    ProviderRegistration,
    UnsupportedProviderError,
)

__all__ = [
    "DefaultsLoaderRegistry",
    "ProviderRegistry",
    "get_provider_registry",
    "ProviderFactoryInterface",
    "ProviderRegistration",
    "UnsupportedProviderError",
]
