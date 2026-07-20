"""Registry of provider-supplied template example generators.

Populated by provider registration modules at startup. Callers resolve the
generator for a specific ``provider_type``; missing providers return ``None``
so the caller can decide fallback behaviour.

Built on the shared :class:`SimpleRegistry` base (register/all/clear/
registered_keys/get_or_none), with a ``get`` override that returns ``None`` on
a miss to satisfy :class:`TemplateExampleGeneratorResolverPort` — the
application layer checks for ``None`` and raises with context.
"""

from __future__ import annotations

from orb.domain.base.ports.template_example_generator_port import TemplateExampleGeneratorPort
from orb.infrastructure.registry.simple_registry import SimpleRegistry


class TemplateExampleGeneratorRegistry(SimpleRegistry[TemplateExampleGeneratorPort]):
    """Registry mapping provider type strings to
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

    _registry_name = "TemplateExampleGeneratorRegistry"
    _store: dict[str, TemplateExampleGeneratorPort] = {}

    @classmethod
    def get(cls, provider_type: str) -> TemplateExampleGeneratorPort | None:  # type: ignore[override]
        """Return the generator for *provider_type*, or ``None`` if not registered.

        Overrides :meth:`SimpleRegistry.get` (which raises on miss) so this
        registry satisfies :class:`TemplateExampleGeneratorResolverPort`, whose
        callers treat an absent generator as a legitimate "not applicable" case
        and raise with their own context.  The parameter name matches the port
        (``provider_type``) so the structural Protocol match holds.
        """
        return cls._store.get(provider_type)

    @classmethod
    def registered_providers(cls) -> list[str]:
        """Return all registered provider type strings."""
        return cls.registered_keys()
