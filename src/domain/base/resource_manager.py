"""
Domain Resource Manager Port - Core resource management interface.

This module defines the canonical domain interface for resource management,
following Clean Architecture and DDD principles. This is the single source
of truth for what resource management means in our domain.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

from domain.base.value_objects import ResourceId


class ResourceType(Enum):
    """Enumeration of supported resource types."""

    COMPUTE_INSTANCE = "compute_instance"
    STORAGE_VOLUME = "storage_volume"
    NETWORK_INTERFACE = "network_interface"
    LOAD_BALANCER = "load_balancer"
    DATABASE = "database"
    CACHE = "cache"


@dataclass(frozen=True)
class ResourceSpecification:
    """
    Domain specification for resource provisioning.

    Immutable value object that describes what resources are needed
    without specifying how they should be provisioned.
    """

    resource_type: ResourceType
    name: str
    configuration: Dict[str, Any]
    tags: Dict[str, str]
    region: Optional[str] = None

    def __post_init__(self):
        """Validate resource specification."""
        if not self.name:
            raise ValueError("Resource name cannot be empty") from e
        if not self.configuration:
            raise ValueError("Resource configuration cannot be empty") from e


@dataclass(frozen=True)
class ResourceAllocation:
    """
    Domain representation of allocated resources.

    Immutable value object that represents successfully allocated resources
    with their identifiers and metadata.
    """

    resource_id: ResourceId
    resource_type: ResourceType
    name: str
    status: str
    metadata: Dict[str, Any]
    provider_specific_data: Dict[str, Any]

    def is_active(self) -> bool:
        """Check if resource is in active state."""
        return self.status.lower() in ["running", "active", "available"]

    def is_provisioning(self) -> bool:
        """Check if resource is still being provisioned."""
        return self.status.lower() in ["pending", "creating", "provisioning"]


class ResourceManagerPort(Protocol):
    """
    Domain port for resource management operations.

    This is the canonical interface that defines what resource management
    means in our domain. All infrastructure implementations must conform
    to this interface.
    """

    async def provision_resources(self, specification: ResourceSpecification) -> ResourceAllocation:
        """
        Provision resources according to specification.

        Args:
            specification: Domain specification of what resources to provision

        Returns:
            ResourceAllocation representing the provisioned resources

        Raises:
            ResourceProvisioningError: If provisioning fails
            InsufficientQuotaError: If quota limits are exceeded
        """
        ...

    async def deprovision_resources(self, allocation: ResourceAllocation) -> None:
        """
        Deprovision previously allocated resources.

        Args:
            allocation: The resource allocation to deprovision

        Raises:
            ResourceNotFoundError: If resources don't exist
            ResourceDeprovisioningError: If deprovisioning fails
        """
        ...

    async def get_resource_status(self, resource_id: ResourceId) -> ResourceAllocation:
        """
        Get current status of a resource.

        Args:
            resource_id: Identifier of the resource

        Returns:
            Current ResourceAllocation with updated status

        Raises:
            ResourceNotFoundError: If resource doesn't exist
        """
        ...

    async def list_resources(
        self, resource_type: Optional[ResourceType] = None
    ) -> List[ResourceAllocation]:
        """
        List all resources, optionally filtered by type.

        Args:
            resource_type: Optional filter by resource type

        Returns:
            List of ResourceAllocation objects
        """
        ...

    async def get_resource_quota(
        self, resource_type: ResourceType, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get resource quota information.

        Args:
            resource_type: Type of resource to check quota for
            region: Optional region to check quota in

        Returns:
            Dictionary containing quota information (used, limit, available)
        """
        ...


class ResourceManagerDomainService(ABC):
    """
    Domain service for complex resource management operations.

    This service orchestrates resource management operations that involve
    multiple resources or complex business logic.
    """

    def __init__(self, resource_manager: ResourceManagerPort):
        """Initialize the instance."""
        self.resource_manager = resource_manager

    @abstractmethod
    async def provision_resource_group(
        self, specifications: List[ResourceSpecification]
    ) -> List[ResourceAllocation]:
        """
        Provision a group of related resources.

        This operation ensures that either all resources are provisioned
        successfully or none are (atomic operation).
        """

    @abstractmethod
    async def migrate_resources(
        self, source_region: str, target_region: str, resource_ids: List[ResourceId]
    ) -> List[ResourceAllocation]:
        """
        Migrate resources from one region to another.

        Complex operation that involves provisioning in target region,
        data migration, and cleanup of source resources.
        """
