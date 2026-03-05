"""Provider template port - focused interface for template operations."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.template.template_aggregate import Template


class ProviderTemplatePort(ABC):
    """Focused port for provider template operations.

    This interface follows ISP by providing only template-related operations,
    allowing clients that only need template management to depend on a minimal interface.
    """

    @abstractmethod
    def get_available_templates(self) -> "list[Template]":
        """Get available templates from provider.

        Returns:
            List of available templates
        """

    @abstractmethod
    def validate_template(self, template: "Template") -> bool:
        """Validate template configuration.

        Args:
            template: Template to validate

        Returns:
            True if template is valid, False otherwise
        """
