"""HostFactory-specific field mappings - moved from generic location."""

from typing import Dict

from infrastructure.logging.logger import get_logger


class HostFactoryFieldMappings:
    """Registry of HostFactory-specific field mappings per provider."""

    # Field mappings organized by provider
    MAPPINGS = {
        # Generic fields (work with any provider)
        "generic": {
            # Core template fields
            "templateId": "template_id",
            "maxNumber": "max_instances",
            "imageId": "image_id",
            "keyName": "key_name",
            # Network configuration
            "subnetId": "subnet_ids",  # Will be converted to list
            "securityGroupIds": "security_group_ids",
            # Instance configuration
            "vmType": "instance_type",
            "vmTypes": "instance_types",
            # Pricing and allocation
            "priceType": "price_type",
            "maxSpotPrice": "max_price",
            "allocationStrategy": "allocation_strategy",
            # Storage configuration
            "rootDeviceVolumeSize": "root_volume_size",
            "volumeType": "root_volume_type",
            "iops": "root_volume_iops",
            # Tags and metadata
            "instanceTags": "tags",  # Will be parsed from string format
        },
        # AWS-specific fields (only mapped when AWS provider is active)
        "aws": {
            # AWS instance type configurations
            "vmTypesOnDemand": "instance_types_ondemand",
            "vmTypesPriority": "instance_types_priority",
            # AWS pricing configurations
            "percentOnDemand": "percent_on_demand",
            "allocationStrategyOnDemand": "allocation_strategy_ondemand",
            # AWS fleet configurations
            "fleetRole": "fleet_role",
            "spotFleetRequestExpiry": "spot_fleet_request_expiry",
            "poolsCount": "pools_count",
            # AWS launch template
            "launchTemplateId": "launch_template_id",
            # AWS instance configuration
            "instanceProfile": "instance_profile",
            "userDataScript": "user_data",
        },
    }

    @classmethod
    def get_mappings(cls, provider_type: str) -> Dict[str, str]:
        """
        Get field mappings for HostFactory + provider combination.

        Args:
            provider_type: Type of provider (e.g., 'aws')

        Returns:
            Dictionary mapping HostFactory field names to internal field names
        """
        logger = get_logger(__name__)

        # Combine generic + provider-specific mappings
        generic_mappings = cls.MAPPINGS.get("generic", {})
        provider_mappings = cls.MAPPINGS.get(provider_type, {})

        # Log the mapping combination being used
        total_mappings = len(generic_mappings) + len(provider_mappings)
        logger.debug(
            "Using %s HostFactory field mappings for %s: %s generic + %s provider-specific",
            total_mappings,
            provider_type,
            len(generic_mappings),
            len(provider_mappings),
        )

        return {**generic_mappings, **provider_mappings}

    @classmethod
    def get_supported_providers(cls) -> list[str]:
        """Get list of supported providers for HostFactory."""
        return [key for key in cls.MAPPINGS.keys() if key != "generic"]

    @classmethod
    def is_provider_specific_field(cls, provider_type: str, field_name: str) -> bool:
        """
        Check if a field is provider-specific for HostFactory.

        Args:
            provider_type: Type of provider
            field_name: Name of the field to check

        Returns:
            True if field is provider-specific, False if generic
        """
        provider_mappings = cls.MAPPINGS.get(provider_type, {})
        return field_name in provider_mappings
