"""Azure template extension configuration."""

from typing import Any, Optional

from pydantic import ConfigDict, Field, model_validator

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
from orb.providers.base.template_extension import ProviderTemplateExtensionBase


class AzureTemplateExtensionConfig(ProviderTemplateExtensionBase):
    """Azure-specific template extension defaults.

    Registered with ``TemplateExtensionRegistry`` so the template factory
    can apply Azure defaults when ``provider_type == "azure"``.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # Azure DTO-only fields preserved by TemplateDTO.provider_config round-trips.
    resource_group: Optional[AzureResourceGroupName] = Field(
        default=None, description="Azure resource group for the template"
    )
    location: Optional[str] = Field(default=None, description="Azure location for the template")
    subscription_id: Optional[str] = Field(
        default=None, description="Azure subscription override for the template"
    )
    vmss_name: Optional[str] = Field(default=None, description="Explicit VMSS name")
    orchestration_mode: Optional[AzureVMSSOrchestrationMode] = Field(
        default=None, description="VMSS orchestration mode"
    )
    platform_fault_domain_count: Optional[int] = Field(
        default=None, description="Fault domain count for Flexible orchestration"
    )
    single_placement_group: Optional[bool] = Field(
        default=None, description="Restrict VMSS to a single placement group"
    )
    image: Optional[AzureImageReference] = Field(
        default=None, description="Azure VM image reference"
    )
    eviction_policy: Optional[AzureEvictionPolicy] = Field(
        default=None, description="Spot eviction policy"
    )
    billing_profile_max_price: Optional[float] = Field(
        default=None, description="Maximum Spot VM price"
    )
    spot_percentage: Optional[int] = Field(
        default=None, description="Desired percentage of Spot VMs"
    )
    base_regular_priority_count: Optional[int] = Field(
        default=None, description="Base regular-priority VM count for priority mix"
    )
    spot_restore_enabled: Optional[bool] = Field(
        default=None, description="Enable Spot Try-Restore"
    )
    spot_restore_timeout: Optional[str] = Field(
        default=None, description="ISO 8601 Spot Try-Restore timeout"
    )
    spot_placement_score_enabled: Optional[bool] = Field(
        default=None, description="Enable Azure Spot Placement Score planning before launch"
    )
    placement_split_strategy: Optional[PlacementSplitStrategy] = Field(
        default=None, description="How Spot Placement Score launches split capacity"
    )
    placement_primary_share_percent: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        description="Capacity percentage assigned to the top placement candidate",
    )
    placement_regions: Optional[list[str]] = Field(
        default=None, description="Azure regions considered for Spot Placement Score planning"
    )
    placement_zones: Optional[list[str]] = Field(
        default=None, description="Azure zones considered for Spot Placement Score planning"
    )
    zones: Optional[list[str]] = Field(default=None, description="Availability zones")
    zone_balance: Optional[bool] = Field(default=None, description="Enable zone balancing")
    proximity_placement_group_id: Optional[AzureProximityPlacementGroupId] = Field(
        default=None, description="Proximity placement group ARM resource ID"
    )
    capacity_reservation_group_id: Optional[AzureCapacityReservationGroupId] = Field(
        default=None, description="Capacity reservation group ARM resource ID"
    )
    os_disk: Optional[AzureOSDiskConfig] = Field(default=None, description="OS disk config")
    data_disks: Optional[list[AzureDataDisk]] = Field(default=None, description="Data disks")
    network_config: Optional[AzureNetworkConfig] = Field(
        default=None, description="Azure networking config"
    )
    security_type: Optional[AzureSecurityType] = Field(default=None, description="VM security type")
    secure_boot_enabled: Optional[bool] = Field(default=None, description="Enable UEFI Secure Boot")
    vtpm_enabled: Optional[bool] = Field(default=None, description="Enable vTPM")
    encryption_at_host: Optional[bool] = Field(
        default=None, description="Enable host-based encryption"
    )
    disk_encryption_set_id: Optional[AzureDiskEncryptionSetId] = Field(
        default=None, description="Disk encryption set ARM resource ID"
    )
    ssh_key_name: Optional[str] = Field(
        default=None, description="Azure SSH Public Key resource name"
    )
    ssh_public_keys: Optional[list[str]] = Field(default=None, description="Inline SSH public keys")
    user_assigned_identity_ids: Optional[list[str]] = Field(
        default=None, description="User-assigned managed identity ARM resource IDs"
    )
    system_assigned_identity: Optional[bool] = Field(
        default=None, description="Enable system-assigned managed identity"
    )
    custom_data: Optional[str] = Field(default=None, description="Base64 custom-data payload")
    extension_profile: Optional[list[dict[str, Any]]] = Field(
        default=None, description="VMSS extension definitions"
    )
    overprovision: Optional[bool] = Field(default=None, description="Enable VMSS overprovisioning")
    upgrade_policy_mode: Optional[AzureUpgradePolicyMode] = Field(
        default=None, description="VMSS upgrade policy mode"
    )
    provider_api_spec: Optional[dict[str, Any]] = Field(
        default=None, description="Raw Azure provider request payload override"
    )
    provider_api_spec_file: Optional[str] = Field(
        default=None, description="Path to a native Azure provider spec file"
    )
    cluster_name: Optional[str] = Field(default=None, description="CycleCloud cluster name")
    node_array: Optional[str] = Field(default=None, description="CycleCloud node array")
    provider_api: Optional[AzureProviderApi] = Field(default=None, description="Azure provider API")

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
    priority: AzurePriority = Field(
        default=AzurePriority.REGULAR, description="Default VM priority"
    )

    # OS disk
    os_disk_type: Optional[str] = Field(default=None, description="Default OS disk type")
    os_disk_size_gb: Optional[int] = Field(
        default=None, description="OS disk size in GiB (None = image default)"
    )

    # Identity
    admin_username: str = Field(default="azureuser", description="Default admin username")

    # Freeform attributes
    node_attributes: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Additional provider attributes; VMSS attributes cannot replace properties "
            "managed by the ARM payload mapper"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def reject_aws_fleet_type(cls, data: Any) -> Any:
        """Reject the AWS-only fleet type instead of silently ignoring it."""
        if isinstance(data, dict) and ({"fleet_type", "fleetType"} & data.keys()):
            raise ValueError("fleet_type is AWS-specific and is not supported by Azure templates")
        return data

    def to_template_defaults(self) -> dict[str, Any]:
        """Project every configured field, normalising legacy OS disk settings."""
        defaults = super().to_template_defaults()
        defaults.pop("os_disk_type", None)
        defaults.pop("os_disk_size_gb", None)

        if self.os_disk is not None:
            defaults["os_disk"] = self.os_disk.model_dump(mode="json", exclude_none=True)
        elif self.os_disk_type is not None or self.os_disk_size_gb is not None:
            legacy_os_disk: dict[str, Any] = {
                "storage_account_type": self.os_disk_type or "Premium_LRS",
            }
            if self.os_disk_size_gb is not None:
                legacy_os_disk["disk_size_gb"] = self.os_disk_size_gb
            defaults["os_disk"] = legacy_os_disk
        return defaults
