"""Infrastructure adapter implementing TemplateExampleGeneratorPort via AWSHandlerFactory."""

from typing import Any, Optional


class AWSTemplateExampleGeneratorAdapter:
    """Generates example templates by delegating to AWSHandlerFactory.

    Implements :class:`~orb.domain.base.ports.template_example_generator_port.TemplateExampleGeneratorPort`
    (structural Protocol).  Registered into
    :class:`~orb.infrastructure.registry.template_example_generator_registry.TemplateExampleGeneratorRegistry`
    under the key ``"aws"`` during provider bootstrap — the ``provider_type``
    discriminator is implicit in the registry key and is not repeated here.
    """

    def __init__(self, aws_handler_factory: Any) -> None:
        self._factory = aws_handler_factory

    def generate_example_templates(
        self,
        provider_name: str,
        provider_api: Optional[str] = None,
    ) -> list[Any]:
        """Generate example templates for the AWS provider."""
        examples = self._factory.generate_example_templates()
        if not examples:
            return []

        if provider_api:
            examples = [t for t in examples if t.provider_api == provider_api]

        return examples
