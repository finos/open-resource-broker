"""
Cloud Resource Manager Port

This module defines the interface for managing cloud resources.
It follows the Port-Adapter pattern from Hexagonal Architecture (Ports and Adapters).
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class CloudResourceManagerPort(ABC):
    """
    Interface for managing cloud resources.

    This port defines the operations needed to manage cloud resources
    without exposing infrastructure-specific details to the domain layer.
    """

    @abstractmethod
    def get_resource_quota(
        self, resource_type: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get quota information for a specific resource type.

        Args:
            resource_type: Type of resource (e.g., 'instances', 'volumes')
            region: Optional region to check quotas for

        Returns:
            Dictionary containing quota information

        Raises:
            InfrastructureError: For infrastructure errors
        """

    @abstractmethod
    def check_resource_availability(
        self, resource_type: str, count: int, region: Optional[str] = None
    ) -> bool:
        """
        Check if the requested number of resources are available.

        Args:
            resource_type: Type of resource (e.g., 'instances', 'volumes')
            count: Number of resources to check
            region: Optional region to check availability for

        Returns:
            True if resources are available, False otherwise

        Raises:
            InfrastructureError: For infrastructure errors
        """

    @abstractmethod
    def get_resource_types(self) -> List[str]:
        """
        Get a list of available resource types.

        Returns:
            List of resource type identifiers

        Raises:
            InfrastructureError: For infrastructure errors
        """

    @abstractmethod
    def get_resource_pricing(
        self, resource_type: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get pricing information for a specific resource type.

        Args:
            resource_type: Type of resource (e.g., 'instances', 'volumes')
            region: Optional region to get pricing for

        Returns:
            Dictionary containing pricing information

        Raises:
            InfrastructureError: For infrastructure errors
        """

    @abstractmethod
    def get_account_id(self) -> str:
        """
        Get the current account identifier.

        Returns:
            Account identifier

        Raises:
            AuthorizationError: If credentials are invalid
            InfrastructureError: For other infrastructure errors
        """

    @abstractmethod
    def validate_credentials(self) -> bool:
        """
        Validate the current credentials.

        Returns:
            True if credentials are valid, False otherwise

        Raises:
            InfrastructureError: For infrastructure errors
        """
