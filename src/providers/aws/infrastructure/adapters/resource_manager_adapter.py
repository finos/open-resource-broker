"""
AWS Resource Manager Adapter using base hierarchy.

This module provides an adapter that bridges the old CloudResourceManagerPort
interface with the new resource manager hierarchy, maintaining
backward compatibility while using the improved architecture.
"""

from typing import Any, Dict, List, Optional

from domain.base.dependency_injection import injectable
from domain.base.ports import ConfigurationPort, LoggingPort
from domain.base.resource_manager import (
    ResourceAllocation,
    ResourceId,
    ResourceSpecification,
    ResourceType,
)
from infrastructure.adapters.ports.cloud_resource_manager_port import (
    CloudResourceManagerPort,
)
from providers.aws.exceptions.aws_exceptions import InfrastructureError
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.managers.aws_resource_manager import AWSResourceManagerImpl


@injectable
class AWSResourceManagerAdapter(CloudResourceManagerPort):
    """
    AWS implementation of the CloudResourceManagerPort interface.

    This adapter provides backward compatibility with the old CloudResourceManagerPort
    interface while using the new AWSResourceManagerImpl internally.
    """

    def __init__(self, aws_client: AWSClient, logger: LoggingPort, config: ConfigurationPort):
        """
        Initialize AWS resource manager adapter.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
            config: Configuration port for accessing configuration
        """
        self._logger = logger
        self._aws_client = aws_client
        self._config = config

        # Use the new resource manager internally
        from providers.aws.configuration.config import AWSProviderConfig

        aws_config = AWSProviderConfig()  # This should be injected in real implementation
        self._resource_manager = AWSResourceManagerImpl(self._aws_client, aws_config, self._logger)

    def get_resource_quota(
        self, resource_type: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get resource quota (legacy interface).

        Adapts the old interface to the new resource manager.
        """
        try:
            # Map legacy resource types to new enum
            resource_type_enum = self._map_legacy_resource_type(resource_type)

            # Use the new method
            import asyncio

            quota_data = asyncio.run(
                self._resource_manager.fetch_resource_quota(resource_type_enum, region)
            )

            return quota_data

        except Exception as e:
            self._logger.error("Failed to get resource quota: %s", str(e))
            raise InfrastructureError(f"Failed to get resource quota: {str(e)}")

    def list_available_resources(
        self, resource_type: Optional[str] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List available resources (legacy interface).

        Adapts the old interface to the new resource manager.
        """
        try:
            # Map legacy resource type to new enum
            resource_type_enum = None
            if resource_type:
                resource_type_enum = self._map_legacy_resource_type(resource_type)

            # Use the new method
            import asyncio

            allocations = asyncio.run(
                self._resource_manager.fetch_resource_list(resource_type_enum)
            )

            # Convert to legacy format
            return [self._allocation_to_legacy_format(allocation) for allocation in allocations]

        except Exception as e:
            self._logger.error("Failed to list resources: %s", str(e))
            raise InfrastructureError(f"Failed to list resources: {str(e)}")

    def create_resource(self, resource_config: Dict[str, Any]) -> str:
        """
        Create resource (legacy interface).

        Adapts the old interface to the new resource manager.
        """
        try:
            # Convert legacy config to new specification
            specification = self._legacy_config_to_specification(resource_config)

            # Use the new method
            import asyncio

            allocation = asyncio.run(self._resource_manager.provision_resources(specification))

            return str(allocation.resource_id)

        except Exception as e:
            self._logger.error("Failed to create resource: %s", str(e))
            raise InfrastructureError(f"Failed to create resource: {str(e)}")

    def delete_resource(self, resource_id: str) -> bool:
        """
        Delete resource (legacy interface).

        Adapts the old interface to the new resource manager.
        """
        try:
            # Get resource allocation first
            import asyncio

            allocation = asyncio.run(
                self._resource_manager.fetch_resource_status(ResourceId(resource_id))
            )

            # Use the new method
            asyncio.run(self._resource_manager.deprovision_resources(allocation))

            return True

        except Exception as e:
            self._logger.error("Failed to delete resource: %s", str(e))
            return False

    def get_resource_status(self, resource_id: str) -> Dict[str, Any]:
        """
        Get resource status (legacy interface).

        Adapts the old interface to the new resource manager.
        """
        try:
            # Use the new method
            import asyncio

            allocation = asyncio.run(
                self._resource_manager.fetch_resource_status(ResourceId(resource_id))
            )

            # Convert to legacy format
            return self._allocation_to_legacy_format(allocation)

        except Exception as e:
            self._logger.error("Failed to get resource status: %s", str(e))
            raise InfrastructureError(f"Failed to get resource status: {str(e)}")

    # Private helper methods for adaptation

    def _map_legacy_resource_type(self, legacy_type: str) -> ResourceType:
        """Map legacy resource type strings to new ResourceType enum."""
        mapping = {
            "instances": ResourceType.COMPUTE_INSTANCE,
            "compute": ResourceType.COMPUTE_INSTANCE,
            "ec2": ResourceType.COMPUTE_INSTANCE,
            "volumes": ResourceType.STORAGE_VOLUME,
            "storage": ResourceType.STORAGE_VOLUME,
            "ebs": ResourceType.STORAGE_VOLUME,
            "network": ResourceType.NETWORK_INTERFACE,
            "loadbalancer": ResourceType.LOAD_BALANCER,
            "database": ResourceType.DATABASE,
            "cache": ResourceType.CACHE,
        }

        return mapping.get(legacy_type.lower(), ResourceType.COMPUTE_INSTANCE)

    def _legacy_config_to_specification(self, config: Dict[str, Any]) -> ResourceSpecification:
        """Convert legacy resource config to new ResourceSpecification."""
        resource_type = self._map_legacy_resource_type(config.get("type", "instances"))

        return ResourceSpecification(
            resource_type=resource_type,
            name=config.get("name", "unnamed-resource"),
            configuration=config.get("configuration", {}),
            tags=config.get("tags", {}),
            region=config.get("region"),
        )

    def _allocation_to_legacy_format(self, allocation: ResourceAllocation) -> Dict[str, Any]:
        """Convert ResourceAllocation to legacy format."""
        return {
            "id": str(allocation.resource_id),
            "name": allocation.name,
            "type": allocation.resource_type.value,
            "status": allocation.status,
            "metadata": allocation.metadata,
            "provider_data": allocation.provider_specific_data,
        }


# Backward compatibility - export the adapter as the old name
AWSResourceManagerPort = AWSResourceManagerAdapter
