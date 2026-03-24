"""Azure VMSS-based template aggregate.

Provides an Azure-specific extension of the core Template that targets
Virtual Machine Scale Sets (VMSS) as the primary provisioning mechanism.

Key Azure VMSS concepts modelled here:
- VM size selection (single or multiple for Spot/Flex)
- Spot / low-priority pricing with eviction policies
- VMSS orchestration mode (Flexible vs Uniform)
- Availability zone spreading
- OS disk & data disk configuration
- Networking (subnet, NSG, accelerated networking)
- Image references (Marketplace, custom, gallery)
- Proximity placement groups & capacity reservations
- User data / custom-data / cloud-init
- Extension profiles (custom script, etc.)
- Overprovisioning and upgrade policies

See: https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/overview
     https://learn.microsoft.com/en-us/rest/api/compute/virtual-machine-scale-sets
"""

from typing import Any, Optional

from pydantic import AliasChoices, ConfigDict, Field, model_validator

from domain.template.template_aggregate import Template
from domain.base.value_objects import AllocationStrategy
from providers.azure.domain.template.value_objects import (
    AzureAllocationStrategy,
    AzureCachingType,
    AzureDataDisk,
    AzureEvictionPolicy,
    AzureImageReference,
    AzureNetworkConfig,
    AzureOSDiskConfig,
    AzureOSDiskType,
    AzurePriority,
    AzureProviderApi,
    AzureSecurityType,
    AzureVMSSOrchestrationMode,
)


# Flexible VMSS network profiles require a Microsoft.Network API version
# in the payload. We may want to make this user configurable in the future?
_VMSS_FLEX_NETWORK_API_VERSION = "2022-11-01"


class AzureTemplate(Template):
    """Azure VMSS-specific template with full Azure compute extensions.

    Extends the provider-agnostic ``Template`` with every configuration
    knob exposed by the Azure VMSS (Flexible orchestration) API so that
    callers can declaratively describe the desired VM fleet.

    Note that defaults set to None will typically use the provider default in Config.py
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        extra="forbid",
    )

    # ------------------------------------------------------------------
    # Provider identification
    # ------------------------------------------------------------------
    provider_api: AzureProviderApi = AzureProviderApi.VMSS

    # ------------------------------------------------------------------
    # Resource group & location
    # ------------------------------------------------------------------
    resource_group: str = Field(
        ...,
        description="Azure resource group for the VMSS.",
        validation_alias=AliasChoices("resource_group", "resourceGroup"),
    )
    location: str = Field(
        ...,
        description="Azure region, e.g. 'eastus2'.",
    )
    subscription_id: Optional[str] = Field(
        default=None,
        description="Azure subscription ID (overrides default).",
        validation_alias=AliasChoices("subscription_id", "subscriptionId"),
    )

    # ------------------------------------------------------------------
    # VMSS configuration
    # ------------------------------------------------------------------
    vmss_name: Optional[str] = Field(
        default=None,
        description="Explicit VMSS name. Auto-generated if omitted.",
        validation_alias=AliasChoices("vmss_name", "vmssName"),
    )
    orchestration_mode: AzureVMSSOrchestrationMode = Field(
        default=AzureVMSSOrchestrationMode.FLEXIBLE,
        description="VMSS orchestration mode.",
        validation_alias=AliasChoices("orchestration_mode", "orchestrationMode"),
    )
    platform_fault_domain_count: Optional[int] = Field(
        default=None,
        ge=1,
        le=5,
        description="Fault domain count for Flexible orchestration.",
        validation_alias=AliasChoices(
            "platform_fault_domain_count", "platformFaultDomainCount"
        ),
    )
    single_placement_group: bool = Field(
        default=False,
        description="Restrict VMSS to a single placement group (max ~100 VMs).",
        validation_alias=AliasChoices("single_placement_group", "singlePlacementGroup"),
    )

    # ------------------------------------------------------------------
    # VM size selection
    # ------------------------------------------------------------------
    vm_size: str = Field(
        ...,
        description="Primary Azure VM size, e.g. 'Standard_D4s_v5'.",
        validation_alias=AliasChoices("vm_size", "vmSize"),
    )
    vm_sizes: Optional[list[str]] = Field(
        default=None,
        description="Additional VM size candidates for Spot / flexible allocation.",
        validation_alias=AliasChoices("vm_sizes", "vmSizes"),
    )

    # ------------------------------------------------------------------
    # Image
    # ------------------------------------------------------------------
    image: Optional[AzureImageReference] = Field(
        default=None,
        description="VM image reference (Marketplace or custom).",
    )

    # ------------------------------------------------------------------
    # Pricing / Spot
    # ------------------------------------------------------------------
    priority: AzurePriority = Field(
        default=AzurePriority.REGULAR,
        description="VM priority (Regular, Spot, Low).",
    )
    eviction_policy: Optional[AzureEvictionPolicy] = Field(
        default=None,
        description="Eviction policy for Spot VMs.",
        validation_alias=AliasChoices("eviction_policy", "evictionPolicy"),
    )
    billing_profile_max_price: Optional[float] = Field(
        default=None,
        description="Max price ($/hr) for Spot VMs. -1 = market price.",
        validation_alias=AliasChoices("billing_profile_max_price", "billingProfileMaxPrice"),
    )
    spot_percentage: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        description=(
            "Desired percentage of Spot VMs above the regular-priority base count. "
            "Mapped to Azure Flexible VMSS priorityMixPolicy."
        ),
        validation_alias=AliasChoices("spot_percentage", "spotPercentage"),
    )
    base_regular_priority_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Minimum number of regular-priority VMs to keep when using Spot Priority Mix."
        ),
        validation_alias=AliasChoices(
            "base_regular_priority_count", "baseRegularPriorityCount"
        ),
    )
    spot_allocation_strategy: Optional[AzureAllocationStrategy] = Field(
        default=None,
        description="Spot allocation strategy (LowestPrice, CapacityOptimized).",
        validation_alias=AliasChoices("spot_allocation_strategy", "spotAllocationStrategy"),
    )
    spot_restore_enabled: bool = Field(
        default=False,
        description="Enable Spot Try-Restore to automatically re-create evicted instances.",
        validation_alias=AliasChoices("spot_restore_enabled", "spotRestoreEnabled"),
    )
    spot_restore_timeout: Optional[str] = Field(
        default=None,
        description="ISO 8601 duration for spot restore timeout, e.g. 'PT1H'.",
        validation_alias=AliasChoices("spot_restore_timeout", "spotRestoreTimeout"),
    )

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    zones: Optional[list[str]] = Field(
        default=None,
        description="Availability zones, e.g. ['1', '2', '3'].",
    )
    zone_balance: bool = Field(
        default=False,
        description="Strictly balance instances across zones.",
        validation_alias=AliasChoices("zone_balance", "zoneBalance"),
    )
    proximity_placement_group_id: Optional[str] = Field(
        default=None,
        description="ARM resource ID of a proximity placement group.",
        validation_alias=AliasChoices(
            "proximity_placement_group_id", "proximityPlacementGroupId"
        ),
    )
    capacity_reservation_group_id: Optional[str] = Field(
        default=None,
        description="ARM resource ID of a capacity reservation group.",
        validation_alias=AliasChoices(
            "capacity_reservation_group_id", "capacityReservationGroupId"
        ),
    )

    # ------------------------------------------------------------------
    # OS & storage
    # ------------------------------------------------------------------
    os_disk: Optional[AzureOSDiskConfig] = Field(
        default=None,
        description="OS disk configuration.",
        validation_alias=AliasChoices("os_disk", "osDisk"),
    )
    data_disks: list[AzureDataDisk] = Field(
        default_factory=list,
        description="Additional data disks.",
        validation_alias=AliasChoices("data_disks", "dataDisks"),
    )

    # ------------------------------------------------------------------
    # Networking
    # ------------------------------------------------------------------
    network_config: Optional[AzureNetworkConfig] = Field(
        default=None,
        description="Primary NIC / subnet / NSG configuration.",
        validation_alias=AliasChoices("network_config", "networkConfig"),
    )

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    security_type: Optional[AzureSecurityType] = Field(
        default=None,
        description="VM security type (TrustedLaunch, ConfidentialVM).",
        validation_alias=AliasChoices("security_type", "securityType"),
    )
    secure_boot_enabled: Optional[bool] = Field(
        default=None,
        description="Enable UEFI Secure Boot (requires TrustedLaunch).",
        validation_alias=AliasChoices("secure_boot_enabled", "secureBootEnabled"),
    )
    vtpm_enabled: Optional[bool] = Field(
        default=None,
        description="Enable vTPM (requires TrustedLaunch).",
        validation_alias=AliasChoices("vtpm_enabled", "vtpmEnabled"),
    )
    encryption_at_host: Optional[bool] = Field(
        default=None,
        description="Enable host-based encryption for all disks.",
        validation_alias=AliasChoices("encryption_at_host", "encryptionAtHost"),
    )
    disk_encryption_set_id: Optional[str] = Field(
        default=None,
        description="ARM resource ID of a disk encryption set (CMK).",
        validation_alias=AliasChoices("disk_encryption_set_id", "diskEncryptionSetId"),
    )

    # ------------------------------------------------------------------
    # Identity & access
    # ------------------------------------------------------------------
    admin_username: str = Field(
        default="azureuser",
        description="VM admin username.",
        validation_alias=AliasChoices("admin_username", "adminUsername"),
    )
    ssh_key_name: Optional[str] = Field(
        default=None,
        description=(
            "Name of an Azure SSH Public Key resource "
            "(Microsoft.Compute/sshPublicKeys) in the same resource group. "
            "The handler resolves the actual key data at provisioning time. "
        ),
        validation_alias=AliasChoices("ssh_key_name", "sshKeyName"),
    )
    ssh_public_keys: list[str] = Field(
        default_factory=list,
        description=(
            "Inline SSH public key strings for Linux VMs. "
            "Prefer ssh_key_name to reference an Azure-managed key instead."
        ),
        validation_alias=AliasChoices("ssh_public_keys", "sshPublicKeys"),
    )
    user_assigned_identity_ids: list[str] = Field(
        default_factory=list,
        description="ARM resource IDs of user-assigned managed identities.",
        validation_alias=AliasChoices(
            "user_assigned_identity_ids", "userAssignedIdentityIds"
        ),
    )
    system_assigned_identity: bool = Field(
        default=False,
        description="Enable system-assigned managed identity.",
        validation_alias=AliasChoices("system_assigned_identity", "systemAssignedIdentity"),
    )

    # ------------------------------------------------------------------
    # Bootstrapping
    # ------------------------------------------------------------------
    custom_data: Optional[str] = Field(
        default=None,
        description="Base64-encoded custom data / cloud-init payload.",
        validation_alias=AliasChoices("custom_data", "customData"),
    )
    extension_profile: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="VMSS VM extension definitions (custom script, monitoring, etc.).",
        validation_alias=AliasChoices("extension_profile", "extensionProfile"),
    )

    # ------------------------------------------------------------------
    # Scale & upgrade behaviour
    # ------------------------------------------------------------------
    overprovision: bool = Field(
        default=False,
        description="Enable overprovisioning (VMSS creates extra VMs then removes surplus).",
    )
    upgrade_policy_mode: str = Field(
        default="Manual",
        description="Upgrade policy: Manual, Rolling, or Automatic.",
        validation_alias=AliasChoices("upgrade_policy_mode", "upgradePolicyMode"),
    )

    # ------------------------------------------------------------------
    # Freeform pass-through
    # ------------------------------------------------------------------
    provider_api_spec: Optional[dict[str, Any]] = Field(
        default=None,
        description="Optional raw Azure provider request payload override/overlay.",
        validation_alias=AliasChoices("provider_api_spec", "providerApiSpec"),
    )
    provider_api_spec_file: Optional[str] = Field(
        default=None,
        description="Path to a JSON native spec file for Azure provider request payloads.",
        validation_alias=AliasChoices("provider_api_spec_file", "providerApiSpecFile"),
    )
    node_attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Freeform pass-through properties appended to the VMSS ARM payload.",
        validation_alias=AliasChoices("node_attributes", "nodeAttributes"),
    )

    # ------------------------------------------------------------------
    # CycleCloud configuration
    # ------------------------------------------------------------------
    cluster_name: Optional[str] = Field(
        default=None,
        description="CycleCloud cluster name to add nodes to.",
        validation_alias=AliasChoices("cluster_name", "clusterName"),
    )
    node_array: str = Field(
        default="execute",
        description="CycleCloud node array (partition) to target, e.g. 'execute', 'hpc', 'htc'.",
        validation_alias=AliasChoices("node_array", "nodeArray"),
    )
    cyclecloud_url: Optional[str] = Field(
        default=None,
        description="CycleCloud REST API base URL, e.g. 'https://cyclecloud.example.com'.",
        validation_alias=AliasChoices("cyclecloud_url", "cyclecloudUrl"),
    )
    cyclecloud_credential_path: Optional[str] = Field(
        default=None,
        description="Path to a JSON file containing CycleCloud credentials and optional auth overrides.",
        validation_alias=AliasChoices("cyclecloud_credential_path", "cyclecloudCredentialPath"),
    )
    cyclecloud_verify_ssl: Optional[bool] = Field(
        default=None,
        description="Whether to verify SSL certificates for CycleCloud API calls.",
        validation_alias=AliasChoices("cyclecloud_verify_ssl", "cyclecloudVerifySsl"),
    )
    # TODO: Bearer functionality not tested in live environment
    cyclecloud_auth_mode: Optional[str] = Field(
        default=None,
        description="CycleCloud auth mode override, e.g. 'basic' or 'bearer'.",
        validation_alias=AliasChoices("cyclecloud_auth_mode", "cyclecloudAuthMode"),
    )
    cyclecloud_aad_scope: Optional[str] = Field(
        default=None,
        description="AAD scope used to resolve a bearer token for CycleCloud.",
        validation_alias=AliasChoices("cyclecloud_aad_scope", "cyclecloudAadScope"),
    )

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(self, **data: Any) -> None:
        """Initialise the Azure template and set provider_type."""
        data["provider_type"] = "azure"
        super().__init__(**data)

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def validate_azure_template(self) -> "AzureTemplate":
        """Azure-specific template validation."""
        if self.provider_api == AzureProviderApi.VMSS_UNIFORM:
            if self.orchestration_mode != AzureVMSSOrchestrationMode.UNIFORM:
                raise ValueError(
                    "provider_api 'VMSSUniform' requires orchestration_mode 'Uniform'"
                )

        if self.allocation_strategy == AllocationStrategy.SPOT_PLACEMENT_SCORE.value:
            candidate_sizes = [self.vm_size, *(self.vm_sizes or [])]
            if len(candidate_sizes) < 2:
                raise ValueError(
                    "spotPlacementScore allocation strategy requires at least two candidate vm sizes"
                )

        if self.spot_percentage is not None:
            if self.provider_api not in (
                AzureProviderApi.VMSS,
                AzureProviderApi.VMSS_UNIFORM,
            ):
                raise ValueError(
                    "spot_percentage is only supported for VMSS-based Azure templates"
                )
            if self.orchestration_mode != AzureVMSSOrchestrationMode.FLEXIBLE:
                raise ValueError(
                    "spot_percentage requires Flexible orchestration mode"
                )
            if self.single_placement_group:
                raise ValueError(
                    "spot_percentage is not supported when single_placement_group is enabled"
                )
            # Azure requires Spot priority on the scale set when using priorityMixPolicy.
            if self.priority == AzurePriority.LOW:
                raise ValueError(
                    "spot_percentage is not compatible with Low priority VMs; use Spot"
                )
            if self.priority == AzurePriority.REGULAR:
                object.__setattr__(self, "priority", AzurePriority.SPOT)

        # Spot VMs require an eviction policy
        if self.priority == AzurePriority.SPOT:
            if self.eviction_policy is None:
                object.__setattr__(self, "eviction_policy", AzureEvictionPolicy.DEALLOCATE)
            if self.spot_allocation_strategy is None:
                object.__setattr__(
                    self,
                    "spot_allocation_strategy",
                    AzureAllocationStrategy.CAPACITY_OPTIMIZED,
                )

        # Non-spot VMs should not have spot-specific settings
        if self.priority == AzurePriority.REGULAR:
            if self.eviction_policy is not None:
                raise ValueError("eviction_policy is only valid for Spot or Low priority VMs")
            if self.billing_profile_max_price is not None:
                raise ValueError(
                    "billing_profile_max_price is only valid for Spot priority VMs"
                )

        # Zone balance requires zones
        if self.zone_balance and not self.zones:
            raise ValueError("zone_balance requires at least one availability zone")

        # overprovision is only valid for Uniform orchestration
        if (
            self.overprovision
            and self.orchestration_mode != AzureVMSSOrchestrationMode.UNIFORM
        ):
            raise ValueError(
                "overprovision is only valid for Uniform orchestration mode"
            )

        # Trusted Launch validation
        if self.security_type == AzureSecurityType.TRUSTED_LAUNCH:
            if self.secure_boot_enabled is None:
                object.__setattr__(self, "secure_boot_enabled", True)
            if self.vtpm_enabled is None:
                object.__setattr__(self, "vtpm_enabled", True)

        # Upgrade policy must be one of the known values
        valid_upgrade_policies = {"Manual", "Rolling", "Automatic"}
        if self.upgrade_policy_mode not in valid_upgrade_policies:
            raise ValueError(
                f"upgrade_policy_mode must be one of {valid_upgrade_policies}, "
                f"got '{self.upgrade_policy_mode}'"
            )

        # Capacity reservation and Spot are mutually exclusive
        if self.capacity_reservation_group_id and self.priority == AzurePriority.SPOT:
            raise ValueError(
                "Capacity reservations cannot be used with Spot priority VMs"
            )

        # SSH access is required for Linux VMs — never fall back to
        # generating a random admin password.  Callers must provide either
        # ssh_key_name (a reference to an Azure SSH Public Key resource) or inline ssh_public_keys.
        # CycleCloud manages SSH access internally so this is not required.
        if self.provider_api != AzureProviderApi.CYCLECLOUD:
            if self.image is None and self.image_id is None:
                raise ValueError(
                    "An Azure image source is required. Provide either 'image' or 'image_id'."
                )
            if not self.ssh_key_name and not self.ssh_public_keys:
                raise ValueError(
                    "SSH access is required for Azure Linux VMs. Provide either "
                    "'ssh_key_name' (name of an Azure SSH Public Key resource) "
                    "or 'ssh_public_keys' (inline key data). "
                    "Password-based authentication is not supported."
                )

        # CycleCloud-specific validation
        if self.provider_api == AzureProviderApi.CYCLECLOUD:
            if not self.cluster_name:
                raise ValueError(
                    "cluster_name is required for CycleCloud templates. "
                    "Specify the name of an existing CycleCloud cluster."
                )

        return self

    @model_validator(mode="after")
    def validate_native_spec_mutual_exclusion(self) -> "AzureTemplate":
        """Validate mutual exclusion of inline and file-based provider specs."""
        if self.provider_api_spec and self.provider_api_spec_file:
            raise ValueError("Cannot specify both provider_api_spec and provider_api_spec_file")
        return self

    # ------------------------------------------------------------------
    # SSH key resolution
    # ------------------------------------------------------------------

    def resolve_ssh_keys(self, compute_client: Any) -> list[str]:
        """Resolve SSH public key data for VM provisioning.

        If *ssh_public_keys* are provided inline they are returned as-is.
        If *ssh_key_name* is set the method fetches the public key data
        from the ``Microsoft.Compute/sshPublicKeys`` resource in the
        template's resource group.

        The resolved keys are also cached back into ``ssh_public_keys``
        so that ``to_azure_api_format()`` can emit them without a
        second API call.

        Args:
            compute_client: An Azure ``ComputeManagementClient`` instance.

        Returns:
            A list of SSH public key strings.

        Raises:
            ValueError: If neither *ssh_key_name* nor *ssh_public_keys*
                is set, or if the named SSH key resource cannot be found.
        """
        # Inline keys take precedence — nothing to resolve.
        if self.ssh_public_keys:
            return list(self.ssh_public_keys)

        if not self.ssh_key_name:
            raise ValueError(
                "Cannot resolve SSH keys: neither 'ssh_key_name' nor "
                "'ssh_public_keys' is set on the template."
            )

        try:
            ssh_resource = compute_client.ssh_public_keys.get(
                resource_group_name=self.resource_group,
                ssh_public_key_name=self.ssh_key_name,
            )
            key_data: str = ssh_resource.public_key
            if not key_data:
                raise ValueError(
                    f"Azure SSH Public Key resource '{self.ssh_key_name}' "
                    f"in resource group '{self.resource_group}' exists but "
                    f"contains no public key data."
                )
        except AttributeError:
            raise ValueError(
                f"Azure SSH Public Key resource '{self.ssh_key_name}' "
                f"in resource group '{self.resource_group}' could not be "
                f"resolved. Ensure the resource exists and the caller has "
                f"'Microsoft.Compute/sshPublicKeys/read' permission."
            )

        # Cache resolved key so downstream serialisation can use it.
        resolved = [key_data]
        object.__setattr__(self, "ssh_public_keys", resolved)
        return resolved

    # ------------------------------------------------------------------
    # ARM / API serialisation helpers
    # ------------------------------------------------------------------

    def to_azure_api_format(self) -> dict[str, Any]:
        """Convert template to the VMSS SDK request payload format.

        Returns a dictionary aligned with the payload accepted by
        ``virtual_machine_scale_sets.begin_create_or_update``.
        """
        properties: dict[str, Any] = {
            "orchestrationMode": self.orchestration_mode.value,
            "singlePlacementGroup": self.single_placement_group,
            "upgradePolicy": {"mode": self.upgrade_policy_mode},
        }

        # overprovision is only valid for Uniform orchestration
        # TODO: Verify
        if self.orchestration_mode == AzureVMSSOrchestrationMode.UNIFORM:
            properties["overprovision"] = self.overprovision

        # platformFaultDomainCount is required for Flexible orchestration
        platform_fault_domain_count = self.platform_fault_domain_count
        if self.orchestration_mode == AzureVMSSOrchestrationMode.FLEXIBLE:
            # Default to 1 if not specified (valid values: 1-5)
            platform_fault_domain_count = platform_fault_domain_count or 1

        if platform_fault_domain_count is not None:
            properties["platformFaultDomainCount"] = platform_fault_domain_count

        # --- Virtual machine profile ---
        vm_profile: dict[str, Any] = {"hardwareProfile": {}}

        # Storage profile
        storage_profile: dict[str, Any] = {}
        if self.image:
            storage_profile["imageReference"] = self.image.to_arm_dict()
        elif self.image_id:
            # Fall back to core image_id field
            storage_profile["imageReference"] = {"id": self.image_id}

        if self.os_disk:
            storage_profile["osDisk"] = self.os_disk.to_arm_dict()
        else:
            # Sensible default
            storage_profile["osDisk"] = {
                "createOption": "FromImage",
                "deleteOption": "Delete",
                "caching": AzureCachingType.READ_WRITE.value,
                "managedDisk": {
                    "storageAccountType": AzureOSDiskType.PREMIUM_LRS.value,
                },
            }

        if self.data_disks:
            storage_profile["dataDisks"] = [d.to_arm_dict() for d in self.data_disks]

        if self.disk_encryption_set_id:
            os_disk_managed = storage_profile.get("osDisk", {}).get("managedDisk")
            if isinstance(os_disk_managed, dict):
                os_disk_managed["diskEncryptionSet"] = {
                    "id": self.disk_encryption_set_id,
                }

            for data_disk in storage_profile.get("dataDisks", []):
                managed_disk = data_disk.get("managedDisk")
                if isinstance(managed_disk, dict):
                    managed_disk["diskEncryptionSet"] = {
                        "id": self.disk_encryption_set_id,
                    }

        vm_profile["storageProfile"] = storage_profile

        # Network profile
        if self.network_config:
            nic_config = self.network_config.to_arm_dict()
            vm_profile["networkProfile"] = {
                "networkInterfaceConfigurations": [nic_config],
                # Flexible VMSS needs an explicit Microsoft.Network API version
                # in the network profile. This is a provider compatibility
                # constant, not a user-configurable template setting.
                "networkApiVersion": _VMSS_FLEX_NETWORK_API_VERSION,
            }

        # OS profile
        os_profile: dict[str, Any] = {
            "computerNamePrefix": (self.vmss_name or self.template_id)[:9],            "adminUsername": self.admin_username,
        }
        if self.custom_data:
            os_profile["customData"] = self.custom_data
        if self.ssh_public_keys:
            os_profile["linuxConfiguration"] = {
                "disablePasswordAuthentication": True,
                "ssh": {
                    "publicKeys": [
                        {
                            "path": f"/home/{self.admin_username}/.ssh/authorized_keys",
                            "keyData": key,
                        }
                        for key in self.ssh_public_keys
                    ],
                },
            }
        vm_profile["osProfile"] = os_profile

        # Priority / Spot
        if self.priority != AzurePriority.REGULAR:
            vm_profile["priority"] = self.priority.value
            if self.eviction_policy:
                vm_profile["evictionPolicy"] = self.eviction_policy.value
            if self.billing_profile_max_price is not None:
                vm_profile["billingProfile"] = {
                    "maxPrice": self.billing_profile_max_price,
                }

        # Security profile
        if self.security_type:
            security_profile: dict[str, Any] = {
                "securityType": self.security_type.value,
            }
            uefi: dict[str, Any] = {}
            if self.secure_boot_enabled is not None:
                uefi["secureBootEnabled"] = self.secure_boot_enabled
            if self.vtpm_enabled is not None:
                uefi["vTpmEnabled"] = self.vtpm_enabled
            if uefi:
                security_profile["uefiSettings"] = uefi
            if self.encryption_at_host is not None:
                security_profile["encryptionAtHost"] = self.encryption_at_host
            vm_profile["securityProfile"] = security_profile

        # Extension profile
        if self.extension_profile:
            vm_profile["extensionProfile"] = {
                "extensions": self.extension_profile,
            }

        properties["virtualMachineProfile"] = vm_profile

        # --- Spot restore policy ---
        if self.spot_restore_enabled:
            spot_restore: dict[str, Any] = {"enabled": True}
            if self.spot_restore_timeout:
                spot_restore["restoreTimeout"] = self.spot_restore_timeout
            properties["spotRestorePolicy"] = spot_restore

        if self.spot_percentage is not None:
            properties["priorityMixPolicy"] = {
                "baseRegularPriorityCount": self.base_regular_priority_count,
                "regularPriorityPercentageAboveBase": 100 - self.spot_percentage,
            }

        # --- Identity ---
        identity: dict[str, Any] | None = None
        if self.system_assigned_identity and self.user_assigned_identity_ids:
            identity = {
                "type": "SystemAssigned, UserAssigned",
                "userAssignedIdentities": {
                    uid: {} for uid in self.user_assigned_identity_ids
                },
            }
        elif self.system_assigned_identity:
            identity = {"type": "SystemAssigned"}
        elif self.user_assigned_identity_ids:
            identity = {
                "type": "UserAssigned",
                "userAssignedIdentities": {
                    uid: {} for uid in self.user_assigned_identity_ids
                },
            }

        # --- Top-level resource ---
        resource: dict[str, Any] = {
            "type": "Microsoft.Compute/virtualMachineScaleSets",
            "name": self.vmss_name or f"vmss-{self.template_id}",
            "location": self.location,
            "sku": {
                "name": "Mix" if self.vm_sizes else self.vm_size,
                "capacity": self.max_instances,
            },
            "properties": properties,
            "tags": self.tags if self.tags else {},
        }

        if self.vm_sizes:
            sku_profile: dict[str, Any] = {
                "vmSizes": [
                    {"name": vm_size}
                    for vm_size in [self.vm_size, *self.vm_sizes]
                ]
            }
            if self.spot_allocation_strategy:
                sku_profile["allocationStrategy"] = self.spot_allocation_strategy.to_arm_value()
            resource["skuProfile"] = sku_profile

        if self.zones:
            resource["zones"] = self.zones

        if identity:
            resource["identity"] = identity

        if self.proximity_placement_group_id:
            properties["proximityPlacementGroup"] = {
                "id": self.proximity_placement_group_id,
            }

        if self.capacity_reservation_group_id:
            properties.setdefault("virtualMachineProfile", vm_profile)[
                "capacityReservation"
            ] = {
                "capacityReservationGroup": {
                    "id": self.capacity_reservation_group_id,
                },
            }

        # Merge any freeform pass-through attributes
        if self.node_attributes:
            properties.update(self.node_attributes)

        return resource

    @classmethod
    def from_azure_format(cls, data: dict[str, Any]) -> "AzureTemplate":
        """Create an AzureTemplate from a flat configuration dict.

        Accepts both snake_case and camelCase keys (via AliasChoices).
        """
        return cls.model_validate(data)

    def __str__(self) -> str:
        """Return string representation."""
        return (
            f"AzureTemplate(id={self.template_id}, vm_size={self.vm_size}, "
            f"location={self.location}, priority={self.priority.value}, "
            f"instances={self.max_instances})"
        )

    def __repr__(self) -> str:
        """Detailed string representation."""
        return (
            f"AzureTemplate(template_id='{self.template_id}', "
            f"vm_size='{self.vm_size}', location='{self.location}', "
            f"resource_group='{self.resource_group}', "
            f"priority='{self.priority.value}', "
            f"orchestration_mode='{self.orchestration_mode.value}', "
            f"max_instances={self.max_instances})"
        )
