"""Registry of provider-supplied template example generators.

Populated by provider registration modules at startup. Callers resolve the
generator for a specific ``provider_type``; missing providers return ``None``
so the caller can decide fallback behaviour.

Follows the same lightweight class-variable pattern as :class:`CLISpecRegistry`
and :class:`DefaultsLoaderRegistry`.
"""

from __future__ import annotations

from orb.domain.base.ports.template_example_generator_port import TemplateExampleGeneratorPort


class TemplateExampleGeneratorRegistry:
    """Simple registry mapping provider type strings to
    :class:`TemplateExampleGeneratorPort` implementations.

    Usage::

        # During provider bootstrap:
        TemplateExampleGeneratorRegistry.register("aws", AWSTemplateExampleGeneratorAdapter(...))

        # At call site:
        generator = TemplateExampleGeneratorRegistry.get(provider_type)
        if generator is None:
            raise ValueError(f"No template generator registered for provider type: {provider_type}")
        templates = generator.generate_example_templates(provider_name, provider_api)
    """

    _generators: dict[str, TemplateExampleGeneratorPort] = {}

    @classmethod
    def register(cls, provider_type: str, generator: TemplateExampleGeneratorPort) -> None:
        """Register a generator for *provider_type*.

        Re-registering the same provider type silently overwrites the previous
        entry.
        """
        cls._generators[provider_type] = generator

    @classmethod
    def get(cls, provider_type: str) -> TemplateExampleGeneratorPort | None:
        """Return the generator for *provider_type*, or ``None`` if not registered."""
        return cls._generators.get(provider_type)

    @classmethod
    def all(cls) -> dict[str, TemplateExampleGeneratorPort]:
        """Return all registered generators keyed by provider type."""
        return dict(cls._generators)

    @classmethod
    def registered_providers(cls) -> list[str]:
        """Return all registered provider type strings."""
        return list(cls._generators.keys())

    @classmethod
    def clear(cls) -> None:
        """Remove all registrations (primarily for use in tests)."""
        cls._generators.clear()
