"""HostFactory-specific field mappings - moved from generic location."""

from orb.infrastructure.logging.logger import get_logger


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
            "fleetType": "fleet_type",
            # Instance configuration
            "vmType": "machine_types",
            "vmTypes": "machine_types",
            # Pricing and allocation
            "priceType": "price_type",
            "maxSpotPrice": "max_price",
            "allocationStrategy": "allocation_strategy",
            # Storage configuration
            "rootDeviceVolumeSize": "root_device_volume_size",
            "volumeType": "volume_type",
            "iops": "iops",
            # Tags and metadata
            "instanceTags": "tags",  # Will be parsed from string format
            # Template metadata
            "name": "name",
            "requestId": "request_id",
            "providerName": "provider_name",
            "providerApi": "provider_api",
            "providerType": "provider_type",
            "createdAt": "created_at",
        },
        # AWS-specific fields (only mapped when AWS provider is active)
        "aws": {
            # AWS VPC network configuration
            "subnetId": "subnet_ids",  # Will be converted to list
            "subnetIds": "subnet_ids",  # Preserve full list when provided
            "securityGroupIds": "security_group_ids",
            # AWS instance type configurations
            "vmTypesOnDemand": "machine_types_ondemand",
            "vmTypesPriority": "machine_types_priority",
            "abisInstanceRequirements": "abis_instance_requirements",
            # AWS pricing configurations
            "percentOnDemand": "percent_on_demand",
            "allocationStrategyOnDemand": "allocation_strategy_on_demand",
            # AWS fleet configurations
            "fleetRole": "fleet_role",
            "spotFleetRequestExpiry": "spot_fleet_request_expiry",
            "poolsCount": "pools_count",
            # AWS instance configuration
            "instanceProfile": "instance_profile",
            "launchTemplateId": "launch_template_id",
            "userDataScript": "user_data",
        },
        # Azure-specific fields (only mapped when Azure provider is active)
        "azure": {
            # Override generic AWS-centric meanings for Azure templates
            "vmType": "vm_size",
            "vmTypes": "vm_sizes",
            "keyName": "ssh_key_name",
            "subnetId": "network_config.subnet_id",
            "securityGroupIds": "network_config.network_security_group_id",
            # Azure resource targeting
            "resourceGroup": "resource_group",
            "subscriptionId": "subscription_id",
            # Azure VMSS / compute configuration
            "vmSize": "vm_size",
            "vmSizes": "vm_sizes",
            "vmSizePreferences": "vm_size_preferences",
            "vmssName": "vmss_name",
            "orchestrationMode": "orchestration_mode",
            "platformFaultDomainCount": "platform_fault_domain_count",
            "singlePlacementGroup": "single_placement_group",
            # Azure pricing / placement
            "evictionPolicy": "eviction_policy",
            "billingProfileMaxPrice": "billing_profile_max_price",
            "spotPercentage": "spot_percentage",
            "baseRegularPriorityCount": "base_regular_priority_count",
            "vmssAllocationStrategy": "vmss_allocation_strategy",
            "spotRestoreEnabled": "spot_restore_enabled",
            "spotRestoreTimeout": "spot_restore_timeout",
            "zoneBalance": "zone_balance",
            "proximityPlacementGroupId": "proximity_placement_group_id",
            "capacityReservationGroupId": "capacity_reservation_group_id",
            # Azure storage / network / security
            "osDisk": "os_disk",
            "dataDisks": "data_disks",
            "networkConfig": "network_config",
            "securityType": "security_type",
            "secureBootEnabled": "secure_boot_enabled",
            "vtpmEnabled": "vtpm_enabled",
            "encryptionAtHost": "encryption_at_host",
            "diskEncryptionSetId": "disk_encryption_set_id",
            # Azure identity / bootstrap
            "adminUsername": "admin_username",
            "sshKeyName": "ssh_key_name",
            "sshPublicKeys": "ssh_public_keys",
            "userAssignedIdentityIds": "user_assigned_identity_ids",
            "systemAssignedIdentity": "system_assigned_identity",
            "customData": "custom_data",
            "extensionProfile": "extension_profile",
            "upgradePolicyMode": "upgrade_policy_mode",
            # Azure native spec / metadata
            "providerApiSpec": "provider_api_spec",
            "providerApiSpecFile": "provider_api_spec_file",
            "nodeAttributes": "node_attributes",
            # Azure CycleCloud
            "clusterName": "cluster_name",
            "nodeArray": "node_array",
            "cyclecloudUrl": "cyclecloud_url",
            "cyclecloudCredentialPath": "cyclecloud_credential_path",
            "cyclecloudVerifySsl": "cyclecloud_verify_ssl",
            "cyclecloudAuthMode": "cyclecloud_auth_mode",
            "cyclecloudAadScope": "cyclecloud_aad_scope",
        },
    }

    @classmethod
    def get_mappings(cls, provider_type: str) -> dict[str, str]:
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
    def apply_aws_defaults(cls, mapped: dict) -> dict:
        """
        Apply AWS-specific setdefault logic that depends on launch_template_id.

        When launch_template_id is set the LT already encodes network config,
        so subnet_ids and security_group_ids should not be defaulted to empty lists.
        """
        mapped.setdefault("max_instances", 1)
        mapped.setdefault("price_type", "ondemand")
        mapped.setdefault("allocation_strategy", "lowestPrice")
        if not mapped.get("launch_template_id"):
            mapped.setdefault("subnet_ids", [])
            mapped.setdefault("security_group_ids", [])
        mapped.setdefault("tags", {})
        return mapped

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
