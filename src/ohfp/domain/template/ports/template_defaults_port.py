"""Template defaults port interface for dependency inversion."""

from abc import ABC, abstractmethod
from typing import Any, Optional


class TemplateDefaultsPort(ABC):
    """Port interface for template defaults resolution."""

    @abstractmethod
    def resolve_template_defaults(
        self,
        template_dict: dict[str, Any],
        provider_instance_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Apply hierarchical defaults to a template dictionary.

        Args:
            template_dict: Raw template data from file
            provider_instance_name: Name of provider instance for context

        Returns:
            Template dictionary with defaults applied
        """

    @abstractmethod
    def resolve_provider_api_default(
        self,
        template_dict: dict[str, Any],
        provider_instance_name: Optional[str] = None,
    ) -> str:
        """
        Resolve provider_api default using hierarchical configuration.

        Args:
            template_dict: Template data
            provider_instance_name: Provider instance name for context

        Returns:
            Resolved provider_api value
        """

    @abstractmethod
    def get_effective_template_defaults(
        self, provider_instance_name: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Get the effective template defaults for a provider instance.

        Args:
            provider_instance_name: Provider instance name

        Returns:
            Merged template defaults
        """
