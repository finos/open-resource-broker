"""Provider registry package - re-exports all public symbols for backward compatibility."""

from providers.registry.provider_registry import ProviderRegistry, get_provider_registry
from providers.registry.types import (
    ProviderFactoryInterface,
    ProviderRegistration,
    UnsupportedProviderError,
)

__all__ = [
    "ProviderRegistry",
    "get_provider_registry",
    "ProviderFactoryInterface",
    "ProviderRegistration",
    "UnsupportedProviderError",
]
