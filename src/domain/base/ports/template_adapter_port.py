"""Template Adapter Port

Defines the interface for template operations across different providers.
This port allows different providers to implement their own template adapters
while maintaining a consistent interface.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from domain.base.contracts.template_contract import (
    TemplateContract,
    TemplateValidationResult,
)


class TemplateAdapterPort(ABC):
    """
    Port interface for template adapters.

    This interface defines the contract that all provider-specific
    template adapters must implement.
    """

    @abstractmethod
    async def get_template_by_id(self, template_id: str) -> Optional[TemplateContract]:
        """
        Get a template by its ID.

        Args:
            template_id: The template identifier

        Returns:
            TemplateContract if found, None otherwise
        """

    @abstractmethod
    async def get_all_templates(self) -> List[TemplateContract]:
        """
        Get all available templates.

        Returns:
            List of all TemplateContract objects
        """

    @abstractmethod
    async def get_templates_by_provider_api(self, provider_api: str) -> List[TemplateContract]:
        """
        Get templates filtered by provider API.

        Args:
            provider_api: The provider API to filter by

        Returns:
            List of TemplateContract objects for the specified provider API
        """

    @abstractmethod
    async def validate_template(self, template: TemplateContract) -> TemplateValidationResult:
        """
        Validate a template configuration.

        Args:
            template: The template to validate

        Returns:
            TemplateValidationResult containing validation results
        """

    @abstractmethod
    async def save_template(self, template: TemplateContract) -> None:
        """
        Save a template.

        Args:
            template: The template to save
        """

    @abstractmethod
    async def delete_template(self, template_id: str) -> None:
        """
        Delete a template.

        Args:
            template_id: The template identifier to delete
        """

    @abstractmethod
    def get_supported_provider_apis(self) -> List[str]:
        """
        Get the list of provider APIs supported by this adapter.

        Returns:
            List of supported provider API names
        """

    @abstractmethod
    def get_adapter_info(self) -> Dict[str, Any]:
        """
        Get information about this adapter.

        Returns:
            Dictionary containing adapter metadata
        """
