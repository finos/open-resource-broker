"""Registry mapping provider types to ProviderDefaultsLoaderPort instances."""

from __future__ import annotations

from orb.domain.base.ports.provider_defaults_loader_port import ProviderDefaultsLoaderPort


class DefaultsLoaderRegistry:
    """Simple class-variable registry mapping provider type strings to
    ``ProviderDefaultsLoaderPort`` implementations.

    Follows the same lightweight pattern as ``CLISpecRegistry`` and
    ``FieldMappingRegistry``.

    Usage::

        # During provider bootstrap:
        DefaultsLoaderRegistry.register("aws", AWSDefaultsLoader())

        # At call site:
        for provider_type, loader in DefaultsLoaderRegistry.all().items():
            defaults = loader.load_defaults()
    """

    _loaders: dict[str, ProviderDefaultsLoaderPort] = {}

    @classmethod
    def register(cls, provider_type: str, loader: ProviderDefaultsLoaderPort) -> None:
        """Register a defaults loader for *provider_type*.

        Registration is idempotent — re-registering the same provider type
        silently overwrites the previous entry.
        """
        cls._loaders[provider_type] = loader

    @classmethod
    def get(cls, provider_type: str) -> ProviderDefaultsLoaderPort | None:
        """Return the loader for *provider_type*, or ``None`` if not registered."""
        return cls._loaders.get(provider_type)

    @classmethod
    def all(cls) -> dict[str, ProviderDefaultsLoaderPort]:
        """Return all registered loaders keyed by provider type."""
        return dict(cls._loaders)

    @classmethod
    def registered_providers(cls) -> list[str]:
        """Return all registered provider type strings."""
        return list(cls._loaders.keys())

    @classmethod
    def clear(cls) -> None:
        """Remove all registrations (primarily for use in tests)."""
        cls._loaders.clear()
