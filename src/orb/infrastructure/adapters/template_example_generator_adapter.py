"""Infrastructure adapter implementing TemplateExampleGeneratorPort via AWSHandlerFactory."""

from typing import Any, Optional

from orb.domain.base.ports.template_example_generator_port import TemplateExampleGeneratorPort


class AWSTemplateExampleGeneratorAdapter(TemplateExampleGeneratorPort):
    """Generates example templates by delegating to AWSHandlerFactory."""

    def __init__(self, aws_handler_factory: Any) -> None:
        self._factory = aws_handler_factory

    def generate_example_templates(
        self,
        provider_type: str,
        provider_name: str,
        provider_api: Optional[str] = None,
    ) -> list[Any]:
        """Generate example templates for the given provider type."""
        if provider_type != "aws":
            return []

        examples = self._factory.generate_example_templates()
        if not examples:
            return []

        if provider_api:
            examples = [t for t in examples if t.provider_api == provider_api]

        return examples
