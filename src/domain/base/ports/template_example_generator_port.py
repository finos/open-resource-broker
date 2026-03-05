"""Domain port for generating example templates from a provider."""

from abc import ABC, abstractmethod
from typing import Any, Optional


class TemplateExampleGeneratorPort(ABC):
    """Port for generating example templates for a given provider type."""

    @abstractmethod
    def generate_example_templates(
        self,
        provider_type: str,
        provider_name: str,
        provider_api: Optional[str] = None,
    ) -> list[Any]:
        """Generate example templates for the given provider.

        Returns a list of template objects (domain or DTO), or an empty list
        if the provider type is not supported.
        """
