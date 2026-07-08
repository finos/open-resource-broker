"""Azure implementation of ``FieldMappingPort`` for the HostFactory scheduler."""

from __future__ import annotations

from orb.infrastructure.scheduler.hostfactory.field_mapping_port import FieldMappingPort


class AzureFieldMapping:
    """Azure-specific field-mapping adapter for the HostFactory scheduler."""

    _PROVIDER_MAPPINGS: dict[str, str] = {
        # Override generic AWS-centric meanings for Azure templates.
        "vmType": "vm_size",
        "vmTypes": "vm_sizes",
        "keyName": "ssh_key_name",
        "subnetId": "network_config.subnet_id",
        "securityGroupIds": "network_config.network_security_group_id",
        # Azure resource targeting.
        "resourceGroup": "resource_group",
        "subscriptionId": "subscription_id",
        # Azure VMSS / compute configuration.
        "vmSize": "vm_size",
        "vmSizes": "vm_sizes",
        "vmSizePreferences": "vm_size_preferences",
        "vmssName": "vmss_name",
        "orchestrationMode": "orchestration_mode",
        "platformFaultDomainCount": "platform_fault_domain_count",
        "singlePlacementGroup": "single_placement_group",
        # Azure pricing / placement.
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
        # Azure storage / network / security.
        "osDisk": "os_disk",
        "dataDisks": "data_disks",
        "networkConfig": "network_config",
        "securityType": "security_type",
        "secureBootEnabled": "secure_boot_enabled",
        "vtpmEnabled": "vtpm_enabled",
        "encryptionAtHost": "encryption_at_host",
        "diskEncryptionSetId": "disk_encryption_set_id",
        # Azure identity / bootstrap.
        "adminUsername": "admin_username",
        "sshKeyName": "ssh_key_name",
        "sshPublicKeys": "ssh_public_keys",
        "userAssignedIdentityIds": "user_assigned_identity_ids",
        "systemAssignedIdentity": "system_assigned_identity",
        "customData": "custom_data",
        "extensionProfile": "extension_profile",
        "upgradePolicyMode": "upgrade_policy_mode",
        # Azure native spec / metadata.
        "providerApiSpec": "provider_api_spec",
        "providerApiSpecFile": "provider_api_spec_file",
        "nodeAttributes": "node_attributes",
        # Azure CycleCloud.
        "clusterName": "cluster_name",
        "nodeArray": "node_array",
        "cyclecloudUrl": "cyclecloud_url",
        "cyclecloudCredentialPath": "cyclecloud_credential_path",
        "cyclecloudVerifySsl": "cyclecloud_verify_ssl",
        "cyclecloudAuthMode": "cyclecloud_auth_mode",
        "cyclecloudAadScope": "cyclecloud_aad_scope",
    }

    def get_mappings(self) -> dict[str, str]:
        """Return Azure-specific HF-field to internal-field name entries."""
        return dict(self._PROVIDER_MAPPINGS)

    def apply_defaults(self, mapped: dict) -> dict:
        """Apply Azure-specific defaults after field mapping."""
        mapped.setdefault("max_instances", 1)
        return mapped

    def derive_attributes(self, machine_type: str | None) -> dict[str, list[str]] | None:
        """Azure does not derive cpu/ram from VM size names in HostFactory output."""
        return None


_: FieldMappingPort = AzureFieldMapping()  # type: ignore[assignment]
