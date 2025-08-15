"""AWS Resource Manager implementation using integrated base hierarchy."""

from typing import Any, Dict, List, Optional

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from domain.base.resource_manager import (
    ResourceAllocation,
    ResourceId,
    ResourceSpecification,
    ResourceType,
)
from infrastructure.base.resource_manager import CloudProviderResourceManager
from providers.aws.configuration.config import AWSProviderConfig
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.infrastructure.dry_run_adapter import aws_dry_run_context


@injectable
class AWSResourceManagerImpl(CloudProviderResourceManager[AWSClient]):
    """AWS implementation of integrated resource manager hierarchy."""

    def __init__(self, aws_client: AWSClient, config: AWSProviderConfig, logger: LoggingPort):
        """Initialize AWS resource manager."""
        super().__init__(aws_client, logger)
        self._config = config

    async def execute_provisioning(
        self, specification: ResourceSpecification
    ) -> ResourceAllocation:
        """Execute AWS-specific resource provisioning."""
        if specification.resource_type == ResourceType.COMPUTE_INSTANCE:
            return await self._provision_compute_instance(specification)
        elif specification.resource_type == ResourceType.STORAGE_VOLUME:
            return await self._provision_storage_volume(specification)
        else:
            raise ValueError(f"Unsupported resource type: {specification.resource_type}")

    async def execute_deprovisioning(self, allocation: ResourceAllocation) -> None:
        """Execute AWS-specific resource deprovisioning."""
        if allocation.resource_type == ResourceType.COMPUTE_INSTANCE:
            await self._deprovision_compute_instance(allocation)
        elif allocation.resource_type == ResourceType.STORAGE_VOLUME:
            await self._deprovision_storage_volume(allocation)
        else:
            raise ValueError(f"Unsupported resource type: {allocation.resource_type}")

    async def fetch_resource_status(self, resource_id: ResourceId) -> ResourceAllocation:
        """Fetch resource status from AWS."""
        # Implementation would call AWS APIs to get resource status
        # This is a simplified version
        try:
            # Example: Get EC2 instance status
            response = await self.provider_client.describe_instances(InstanceIds=[str(resource_id)])

            if not response.get("Reservations"):
                raise ValueError(f"Resource not found: {resource_id}")

            instance = response["Reservations"][0]["Instances"][0]

            return ResourceAllocation(
                resource_id=resource_id,
                resource_type=ResourceType.COMPUTE_INSTANCE,
                name=instance.get("Tags", {}).get("Name", "Unknown"),
                status=instance["State"]["Name"],
                metadata={"instance_type": instance["InstanceType"]},
                provider_specific_data=instance,
            )

        except Exception as e:
            self.logger.error(f"Failed to fetch resource status: {str(e)}")
            raise

    async def fetch_resource_list(
        self, resource_type: Optional[ResourceType] = None
    ) -> List[ResourceAllocation]:
        """Fetch list of resources from AWS."""
        resources = []

        try:
            if resource_type is None or resource_type == ResourceType.COMPUTE_INSTANCE:
                instances = await self._list_compute_instances()
                resources.extend(instances)

            if resource_type is None or resource_type == ResourceType.STORAGE_VOLUME:
                volumes = await self._list_storage_volumes()
                resources.extend(volumes)

            return resources

        except Exception as e:
            self.logger.error(f"Failed to list resources: {str(e)}")
            raise

    async def fetch_resource_quota(
        self, resource_type: ResourceType, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch resource quota from AWS."""
        try:
            if resource_type == ResourceType.COMPUTE_INSTANCE:
                return await self._get_compute_quota(region)
            elif resource_type == ResourceType.STORAGE_VOLUME:
                return await self._get_storage_quota(region)
            else:
                return {"used": 0, "limit": 1000, "available": 1000}  # Default quota

        except Exception as e:
            self.logger.error(f"Failed to fetch quota: {str(e)}")
            raise

    # Private implementation methods

    async def _provision_compute_instance(
        self, specification: ResourceSpecification
    ) -> ResourceAllocation:
        """Provision EC2 compute instance."""
        with aws_dry_run_context():
            # Implementation would call AWS EC2 APIs (mocked if dry-run is active)
            # This is a simplified version
            instance_config = specification.configuration

            # Simulate instance creation
            resource_id = ResourceId(f"i-{hash(specification.name) % 1000000:06d}")

            return ResourceAllocation(
                resource_id=resource_id,
                resource_type=ResourceType.COMPUTE_INSTANCE,
                name=specification.name,
                status="pending",
                metadata=instance_config,
                provider_specific_data={"aws_region": specification.region or "us-east-1"},
            )

    async def _provision_storage_volume(
        self, specification: ResourceSpecification
    ) -> ResourceAllocation:
        """Provision EBS storage volume."""
        # Implementation would call AWS EBS APIs
        volume_config = specification.configuration

        # Simulate volume creation
        resource_id = ResourceId(f"vol-{hash(specification.name) % 1000000:06d}")

        return ResourceAllocation(
            resource_id=resource_id,
            resource_type=ResourceType.STORAGE_VOLUME,
            name=specification.name,
            status="creating",
            metadata=volume_config,
            provider_specific_data={"aws_region": specification.region or "us-east-1"},
        )

    async def _deprovision_compute_instance(self, allocation: ResourceAllocation) -> None:
        """Deprovision EC2 compute instance."""
        # Implementation would call AWS EC2 terminate APIs
        self.logger.info(f"Terminating EC2 instance: {allocation.resource_id}")

    async def _deprovision_storage_volume(self, allocation: ResourceAllocation) -> None:
        """Deprovision EBS storage volume."""
        # Implementation would call AWS EBS delete APIs
        self.logger.info(f"Deleting EBS volume: {allocation.resource_id}")

    async def _list_compute_instances(self) -> List[ResourceAllocation]:
        """List EC2 compute instances."""
        # Implementation would call AWS EC2 describe APIs
        return []

    async def _list_storage_volumes(self) -> List[ResourceAllocation]:
        """List EBS storage volumes."""
        # Implementation would call AWS EBS describe APIs
        return []

    async def _get_compute_quota(self, region: Optional[str] = None) -> Dict[str, Any]:
        """Get EC2 compute quota."""
        # Implementation would call AWS Service Quotas APIs
        return {"used": 5, "limit": 100, "available": 95}

    async def _get_storage_quota(self, region: Optional[str] = None) -> Dict[str, Any]:
        """Get EBS storage quota."""
        # Implementation would call AWS Service Quotas APIs
        return {"used": 10, "limit": 1000, "available": 990}


# Backward compatibility alias
AWSResourceManager = AWSResourceManagerImpl
