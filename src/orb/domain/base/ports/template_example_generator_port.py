"""Domain port for generating example templates from a provider."""

from typing import Any, Optional, Protocol


class TemplateExampleGeneratorPort(Protocol):
    """Port for generating example templates for a given provider type.

    Each concrete implementation is provider-specific and registered into
    :class:`~orb.infrastructure.registry.template_example_generator_registry.TemplateExampleGeneratorRegistry`
    keyed by provider type.  The ``provider_type`` discriminator argument has
    therefore been removed — callers resolve the correct adapter from the
    registry before calling this method.
    """

    def generate_example_templates(
        self,
        provider_name: str,
        provider_api: Optional[str] = None,
    ) -> list[Any]:  # type: ignore[return]
        pass
