"""
Resource Provisioning Port

This module defines the interface for provisioning cloud resources.
It follows the Port-Adapter pattern from Hexagonal Architecture (Ports and Adapters).
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from src.domain.request.aggregate import Request
from src.domain.template.aggregate import Template


class ResourceProvisioningPort(ABC):
    """
    Interface for provisioning cloud resources.

    This port defines the operations needed to provision and manage cloud resources
    without exposing infrastructure-specific details to the domain layer.
    """

    @abstractmethod
    async def provision_resources(self, request: Request, template: Template) -> str:
        """
        Provision resources based on the request and template.

        Args:
            request: The request containing provisioning details
            template: The template to use for provisioning

        Returns:
            str: Resource identifier (e.g., fleet ID, ASG name)

        Raises:
            ValidationError: If the template is invalid
            QuotaExceededError: If resource quotas would be exceeded
            InfrastructureError: For other infrastructure errors
        """

    @abstractmethod
    def check_resources_status(self, request: Request) -> List[Dict[str, Any]]:
        """
        Check the status of provisioned resources.

        Args:
            request: The request containing resource identifier

        Returns:
            List of resource details

        Raises:
            EntityNotFoundError: If the resource is not found
            InfrastructureError: For other infrastructure errors
        """

    @abstractmethod
    def release_resources(self, request: Request) -> None:
        """
        Release provisioned resources.

        Args:
            request: The request containing resource identifier

        Raises:
            EntityNotFoundError: If the resource is not found
            InfrastructureError: For other infrastructure errors
        """

    @abstractmethod
    def get_resource_health(self, resource_id: str) -> Dict[str, Any]:
        """
        Get health information for a specific resource.

        Args:
            resource_id: Resource identifier

        Returns:
            Dictionary containing health information

        Raises:
            EntityNotFoundError: If the resource is not found
            InfrastructureError: For other infrastructure errors
        """
