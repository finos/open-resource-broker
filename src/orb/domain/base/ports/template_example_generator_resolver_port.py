"""Domain port for resolving template example generators by provider type."""

from typing import Optional, Protocol

from orb.domain.base.ports.template_example_generator_port import TemplateExampleGeneratorPort


class TemplateExampleGeneratorResolverPort(Protocol):
    """Port for resolving a :class:`TemplateExampleGeneratorPort` by provider type.

    The application layer depends on this port; the infrastructure layer wires
    :class:`~orb.infrastructure.registry.template_example_generator_registry.TemplateExampleGeneratorRegistry`
    as the concrete implementation so that the application service never
    references an infrastructure class directly.
    """

    def get(self, provider_type: str) -> Optional[TemplateExampleGeneratorPort]:  # type: ignore[return]
        """Return the generator for *provider_type*, or ``None`` if not registered."""
        pass
