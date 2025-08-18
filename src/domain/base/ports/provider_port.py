"""Domain port for provider operations."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from domain.machine.aggregate import Machine
from domain.request.aggregate import Request
from domain.template.aggregate import Template


class ProviderPort(ABC):
    """Domain port for provider operations."""

    @abstractmethod
    def provision_resources(self, request: Request) -> List[Machine]:
        """Provision resources based on request."""

    @abstractmethod
    def terminate_resources(self, machine_ids: List[str]) -> None:
        """Terminate resources by machine IDs."""

    @abstractmethod
    def get_available_templates(self) -> List[Template]:
        """Get available templates from provider."""

    @abstractmethod
    def validate_template(self, template: Template) -> bool:
        """Validate template configuration."""

    @abstractmethod
    def get_resource_status(self, machine_ids: List[str]) -> Dict[str, Any]:
        """Get status of resources."""

    @abstractmethod
    def get_provider_info(self) -> Dict[str, Any]:
        """Get provider information."""
