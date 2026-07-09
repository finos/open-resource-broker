"""Azure template extension configuration."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from orb.providers.azure.domain.template.value_objects import (
    AzureAllocationStrategy,
    AzureCapacityReservationGroupId,
    AzureDataDisk,
    AzureDiskEncryptionSetId,
    AzureEvictionPolicy,
    AzureImageReference,
    AzureNetworkConfig,
    AzureOSDiskConfig,
    AzurePriority,
    AzureProviderApi,
    AzureProximityPlacementGroupId,
    AzureResourceGroupName,
    AzureSecurityType,
    AzureUpgradePolicyMode,
    AzureVmSizePreference,
    AzureVMSSOrchestrationMode,
)
from orb.providers.azure.services.spot_placement_planner import PlacementSplitStrategy


class AzureTemplateExtensionConfig(BaseModel):
    """Azure-specific template extension defaults.

    Registered with ``TemplateExtensionRegistry`` so the template factory
    can apply Azure defaults when ``provider_type == "azure"``.
    """

    model_config = ConfigDict(populate_by_name=True)

    # Azure DTO-only fields preserved by TemplateDTO.provider_config round-trips.
    resource_group: Optional[AzureResourceGroupName] = Field(
        None, description="Azure resource group for the template"
    )
    location: Optional[str] = Field(None, description="Azure location for the template")
    subscription_id: Optional[str] = Field(
        None, description="Azure subscription override for the template"
    )
    vmss_name: Optional[str] = Field(None, description="Explicit VMSS name")
    orchestration_mode: Optional[AzureVMSSOrchestrationMode] = Field(
        None, description="VMSS orchestration mode"
    )
    platform_fault_domain_count: Optional[int] = Field(
        None, description="Fault domain count for Flexible orchestration"
    )
    single_placement_group: Optional[bool] = Field(
        None, description="Restrict VMSS to a single placement group"
    )
    image: Optional[AzureImageReference] = Field(None, description="Azure VM image reference")
    eviction_policy: Optional[AzureEvictionPolicy] = Field(None, description="Spot eviction policy")
    billing_profile_max_price: Optional[float] = Field(None, description="Maximum Spot VM price")
    spot_percentage: Optional[int] = Field(None, description="Desired percentage of Spot VMs")
    base_regular_priority_count: Optional[int] = Field(
        None, description="Base regular-priority VM count for priority mix"
    )
    spot_restore_enabled: Optional[bool] = Field(None, description="Enable Spot Try-Restore")
    spot_restore_timeout: Optional[str] = Field(
        None, description="ISO 8601 Spot Try-Restore timeout"
    )
    spot_placement_score_enabled: Optional[bool] = Field(
        None, description="Enable Azure Spot Placement Score planning before launch"
    )
    placement_split_strategy: Optional[PlacementSplitStrategy] = Field(
        None, description="How Spot Placement Score launches split capacity"
    )
    placement_primary_share_percent: Optional[int] = Field(
        None, description="Capacity percentage assigned to the top placement candidate"
    )
    placement_regions: Optional[list[str]] = Field(
        None, description="Azure regions considered for Spot Placement Score planning"
    )
    placement_zones: Optional[list[str]] = Field(
        None, description="Azure zones considered for Spot Placement Score planning"
    )
    zones: Optional[list[str]] = Field(None, description="Availability zones")
    zone_balance: Optional[bool] = Field(None, description="Enable zone balancing")
    proximity_placement_group_id: Optional[AzureProximityPlacementGroupId] = Field(
        None, description="Proximity placement group ARM resource ID"
    )
    capacity_reservation_group_id: Optional[AzureCapacityReservationGroupId] = Field(
        None, description="Capacity reservation group ARM resource ID"
    )
    os_disk: Optional[AzureOSDiskConfig] = Field(None, description="OS disk config")
    data_disks: Optional[list[AzureDataDisk]] = Field(None, description="Data disks")
    network_config: Optional[AzureNetworkConfig] = Field(
        None, description="Azure networking config"
    )
    security_type: Optional[AzureSecurityType] = Field(None, description="VM security type")
    secure_boot_enabled: Optional[bool] = Field(None, description="Enable UEFI Secure Boot")
    vtpm_enabled: Optional[bool] = Field(None, description="Enable vTPM")
    encryption_at_host: Optional[bool] = Field(None, description="Enable host-based encryption")
    disk_encryption_set_id: Optional[AzureDiskEncryptionSetId] = Field(
        None, description="Disk encryption set ARM resource ID"
    )
    ssh_key_name: Optional[str] = Field(None, description="Azure SSH Public Key resource name")
    ssh_public_keys: Optional[list[str]] = Field(None, description="Inline SSH public keys")
    user_assigned_identity_ids: Optional[list[str]] = Field(
        None, description="User-assigned managed identity ARM resource IDs"
    )
    system_assigned_identity: Optional[bool] = Field(
        None, description="Enable system-assigned managed identity"
    )
    custom_data: Optional[str] = Field(None, description="Base64 custom-data payload")
    extension_profile: Optional[list[dict[str, Any]]] = Field(
        None, description="VMSS extension definitions"
    )
    overprovision: Optional[bool] = Field(None, description="Enable VMSS overprovisioning")
    upgrade_policy_mode: Optional[AzureUpgradePolicyMode] = Field(
        None, description="VMSS upgrade policy mode"
    )
    provider_api_spec: Optional[dict[str, Any]] = Field(
        None, description="Raw Azure provider request payload override"
    )
    provider_api_spec_file: Optional[str] = Field(
        None, description="Path to a native Azure provider spec file"
    )
    cluster_name: Optional[str] = Field(None, description="CycleCloud cluster name")
    node_array: Optional[str] = Field(None, description="CycleCloud node array")
    cyclecloud_url: Optional[str] = Field(None, description="CycleCloud API URL")
    cyclecloud_credential_path: Optional[str] = Field(
        None, description="CycleCloud credential reference path"
    )
    cyclecloud_verify_ssl: Optional[bool] = Field(
        None, description="CycleCloud SSL verification setting"
    )
    cyclecloud_auth_mode: Optional[str] = Field(None, description="CycleCloud auth mode override")
    cyclecloud_aad_scope: Optional[str] = Field(None, description="CycleCloud AAD scope")
    provider_api: Optional[AzureProviderApi] = Field(None, description="Azure provider API")

    # VM configuration
    vm_size: Optional[str] = Field(
        default=None,
        description="Explicit Azure VM size default",
    )
    vm_sizes: Optional[list[str]] = Field(
        default=None,
        description="Additional Azure VM size candidates for generic instance mix",
    )
    vm_size_preferences: Optional[list[AzureVmSizePreference]] = Field(
        default=None,
        description="Ranked Azure VM size candidates for Prioritized VMSS instance mix",
    )
    vmss_allocation_strategy: Optional[AzureAllocationStrategy] = Field(
        default=None,
        description="Azure VMSS instance-mix allocation strategy",
    )

    # Pricing
    priority: AzurePriority = Field(AzurePriority.REGULAR, description="Default VM priority")

    # OS disk
    os_disk_type: Optional[str] = Field(None, description="Default OS disk type")
    os_disk_size_gb: Optional[int] = Field(
        None, description="OS disk size in GiB (None = image default)"
    )

    # Identity
    admin_username: str = Field("azureuser", description="Default admin username")

    # Freeform attributes
    node_attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Freeform attributes merged into the template",
    )

    def to_template_defaults(self) -> dict[str, Any]:
        """Convert extension to a dict of default values for template creation."""
        defaults: dict[str, Any] = {
            "priority": self.priority,
            "admin_username": self.admin_username,
            "node_attributes": self.node_attributes,
        }
        if self.vm_size:
            defaults["vm_size"] = self.vm_size
        if self.vm_sizes:
            defaults["vm_sizes"] = self.vm_sizes
        if self.vm_size_preferences:
            defaults["vm_size_preferences"] = self.vm_size_preferences
        if self.vmss_allocation_strategy:
            defaults["vmss_allocation_strategy"] = self.vmss_allocation_strategy
        if self.spot_placement_score_enabled is not None:
            defaults["spot_placement_score_enabled"] = self.spot_placement_score_enabled
        if self.placement_split_strategy:
            defaults["placement_split_strategy"] = self.placement_split_strategy
        if self.placement_primary_share_percent is not None:
            defaults["placement_primary_share_percent"] = self.placement_primary_share_percent
        if self.placement_regions:
            defaults["placement_regions"] = self.placement_regions
        if self.placement_zones:
            defaults["placement_zones"] = self.placement_zones
        if self.os_disk_size_gb is not None:
            defaults["os_disk"] = {
                "storage_account_type": self.os_disk_type or "Premium_LRS",
                "disk_size_gb": self.os_disk_size_gb,
            }
        return defaults
