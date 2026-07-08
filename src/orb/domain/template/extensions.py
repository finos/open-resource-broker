"""Abstract base for provider-specific template extensions."""

from abc import ABC, abstractmethod
from typing import Any


class TemplateExtension(ABC):
    """Abstract base class for template extensions."""

    @abstractmethod
    def to_template_defaults(self) -> dict[str, Any]:
        """Convert extension to template defaults format."""

    @abstractmethod
    def get_provider_type(self) -> str:
        """Get the provider type this extension supports."""
