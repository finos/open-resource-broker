"""
Resource Provisioning Port

This module defines the interface for provisioning cloud resources.
It follows the Port-Adapter pattern from Hexagonal Architecture (Ports and Adapters).
"""

from abc import ABC, abstractmethod

from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template


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
    def release_resources(
        self,
        machine_ids: list[str],
        template_id: str,
        provider_api: str,
        context: dict = None,  # type: ignore[assignment]
    ) -> None:
        """
        Release provisioned resources.

        Args:
            request: The request containing resource identifier

        Raises:
            EntityNotFoundError: If the resource is not found
            InfrastructureError: For other infrastructure errors
        """
