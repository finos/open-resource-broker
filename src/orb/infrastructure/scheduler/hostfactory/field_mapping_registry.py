"""Registry mapping provider types to FieldMappingPort instances."""

from orb.infrastructure.scheduler.hostfactory.field_mapping_port import FieldMappingPort


class FieldMappingRegistry:
    """Simple class-variable registry mapping provider type strings to
    ``FieldMappingPort`` implementations.

    Follows the same lightweight pattern as ``CLISpecRegistry``.

    Usage::

        # During provider bootstrap:
        FieldMappingRegistry.register("aws", AWSFieldMapping())

        # At call site:
        adapter = FieldMappingRegistry.get("aws")
        if adapter is not None:
            mapped = adapter.apply_defaults(mapped)
    """

    _adapters: dict[str, FieldMappingPort] = {}

    @classmethod
    def register(cls, provider_type: str, adapter: FieldMappingPort) -> None:
        """Register a field-mapping adapter for *provider_type*.

        Registration is idempotent — re-registering the same provider type
        silently overwrites the previous entry.
        """
        cls._adapters[provider_type] = adapter

    @classmethod
    def get(cls, provider_type: str) -> FieldMappingPort | None:
        """Return the adapter for *provider_type*, or ``None`` if not registered."""
        return cls._adapters.get(provider_type)

    @classmethod
    def registered_providers(cls) -> list[str]:
        """Return all registered provider type strings."""
        return list(cls._adapters.keys())

    @classmethod
    def clear(cls) -> None:
        """Remove all registrations (primarily for use in tests)."""
        cls._adapters.clear()
