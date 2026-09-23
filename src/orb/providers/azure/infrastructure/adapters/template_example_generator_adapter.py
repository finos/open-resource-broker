"""Azure adapter for provider-owned template example generation."""

from typing import Any, Optional

from orb.providers.azure.infrastructure.azure_handler_factory import (
    generate_azure_example_templates,
)


class AzureTemplateExampleGeneratorAdapter:
    """Expose Azure handler examples through the shared generator port."""

    def generate_example_templates(
        self,
        provider_name: str,
        provider_api: Optional[str] = None,
    ) -> list[Any]:
        """Return Azure examples, optionally restricted to one provider API."""
        examples = generate_azure_example_templates()
        if provider_api is None:
            return examples
        return [example for example in examples if example.get("provider_api") == provider_api]
