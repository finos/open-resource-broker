"""Azure-specific value objects and domain primitives for VMSS-based provisioning."""

from enum import Enum
from typing import Any, Optional

from pydantic import ConfigDict, Field, ValidationInfo, field_validator, model_validator

from orb.domain.base.value_objects import AllocationStrategy, PriceType, ValueObject


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AzureProviderApi(str, Enum):
    """Azure compute provisioning API types.

    Maps to the Azure SDK / ARM API surface used to create VMs.
    """

    VMSS = "VMSS"  # Virtual Machine Scale Set (Flexible orchestration)
    VMSS_UNIFORM = "VMSSUniform"  # VMSS Uniform orchestration (legacy)
    SINGLE_VM = "SingleVM"  # Individual VM via compute API
    CYCLECLOUD = "CycleCloud"  # Azure CycleCloud cluster node management


class AzureVMSSOrchestrationMode(str, Enum):
    """VMSS orchestration mode.

    See: https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/
         virtual-machine-scale-sets-orchestration-modes
    """

    FLEXIBLE = "Flexible"
    UNIFORM = "Uniform"


class AzurePriority(str, Enum):
    """VM priority (maps to core PriceType).

    See: https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms
    """

    REGULAR = "Regular"
    SPOT = "Spot"
    LOW = "Low"  # Legacy low-priority (batch workloads)

    @classmethod
    def from_price_type(cls, price_type: PriceType) -> "AzurePriority":
        """Convert core PriceType to Azure priority."""
        mapping = {
            PriceType.ONDEMAND: cls.REGULAR,
            PriceType.SPOT: cls.SPOT,
            PriceType.RESERVED: cls.REGULAR,  # Reserved still uses Regular VMs
            PriceType.HETEROGENEOUS: cls.REGULAR,
        }
        return mapping.get(price_type, cls.REGULAR)


class AzureEvictionPolicy(str, Enum):
    """Eviction policy for Spot / low-priority VMs.

    See: https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms#eviction-policy
    """

    DEALLOCATE = "Deallocate"
    DELETE = "Delete"


class AzureAllocationStrategy(str, Enum):
    """Spot allocation / placement strategy for VMSS.

    See: https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/
         virtual-machine-scale-sets-use-spot#placement-groups
    """

    LOWEST_PRICE = "LowestPrice"
    CAPACITY_OPTIMIZED = "CapacityOptimized"

    @classmethod
    def from_core(cls, strategy: AllocationStrategy) -> "AzureAllocationStrategy":
        """Map a core AllocationStrategy to Azure-specific value."""
        mapping = {
            AllocationStrategy.LOWEST_PRICE: cls.LOWEST_PRICE,
            AllocationStrategy.CAPACITY_OPTIMIZED: cls.CAPACITY_OPTIMIZED,
            AllocationStrategy.CAPACITY_OPTIMIZED_PRIORITIZED: cls.CAPACITY_OPTIMIZED,
            AllocationStrategy.PRICE_CAPACITY_OPTIMIZED: cls.CAPACITY_OPTIMIZED,
            AllocationStrategy.DIVERSIFIED: cls.LOWEST_PRICE,
        }
        return mapping.get(strategy, cls.LOWEST_PRICE)

    def to_arm_value(self) -> str:
        """Return the value expected by the ARM / REST API."""
        return self.value


class AzureOSDiskType(str, Enum):
    """Managed disk storage account type.

    See: https://learn.microsoft.com/en-us/azure/virtual-machines/disks-types
    """

    STANDARD_LRS = "Standard_LRS"  # Standard HDD
    STANDARD_SSD_LRS = "StandardSSD_LRS"  # Standard SSD
    PREMIUM_LRS = "Premium_LRS"  # Premium SSD
    PREMIUM_SSD_V2_LRS = "PremiumV2_LRS"  # Premium SSD v2
    ULTRA_SSD_LRS = "UltraSSD_LRS"  # Ultra Disk


class AzureSecurityType(str, Enum):
    """VM security type.

    See: https://learn.microsoft.com/en-us/azure/virtual-machines/trusted-launch
    """

    STANDARD = "Standard"
    TRUSTED_LAUNCH = "TrustedLaunch"
    CONFIDENTIAL_VM = "ConfidentialVM"


class AzureCachingType(str, Enum):
    """OS / data disk caching type."""

    NONE = "None"
    READ_ONLY = "ReadOnly"
    READ_WRITE = "ReadWrite"


# ---------------------------------------------------------------------------
# Value Objects
# ---------------------------------------------------------------------------


class AzureImageReference(ValueObject):
    """Azure VM image reference – either a Marketplace image or a custom/gallery image.

    See: https://learn.microsoft.com/en-us/azure/virtual-machines/linux/cli-ps-findimage
    """

    model_config = ConfigDict(populate_by_name=True)

    publisher: Optional[str] = None  # e.g. "Canonical"
    offer: Optional[str] = None  # e.g. "0001-com-ubuntu-server-jammy"
    sku: Optional[str] = None  # e.g. "22_04-lts-gen2"
    version: str = "latest"

    # For custom / Shared Image Gallery / Community Gallery images
    image_id: Optional[str] = Field(
        default=None,
        description="Full resource ID of a custom image, shared image gallery image, "
        "or community gallery image.",
    )

    @model_validator(mode="after")
    def _validate_image_source(self) -> "AzureImageReference":
        """Ensure either marketplace fields or image_id is provided."""
        has_marketplace = self.publisher and self.offer and self.sku
        if not has_marketplace and not self.image_id:
            raise ValueError(
                "Provide either (publisher, offer, sku) for a Marketplace image "
                "or image_id for a custom / gallery image."
            )
        if has_marketplace and self.image_id:
            raise ValueError(
                "Cannot specify both Marketplace image fields (publisher/offer/sku) "
                "and image_id at the same time."
            )
        return self

    def to_arm_dict(self) -> dict[str, Any]:
        """Serialise to the ARM imageReference format."""
        if self.image_id:
            return {"id": self.image_id}
        return {
            "publisher": self.publisher,
            "offer": self.offer,
            "sku": self.sku,
            "version": self.version,
        }


class AzureNetworkConfig(ValueObject):
    """Networking configuration for VMSS instances."""

    model_config = ConfigDict(populate_by_name=True)

    subnet_id: str = Field(
        ...,
        description="Full ARM resource ID of the subnet.",
    )
    network_security_group_id: Optional[str] = Field(
        default=None,
        description="Full ARM resource ID of the NSG to attach.",
    )
    accelerated_networking: Optional[bool] = Field(
        default=None,
        description="Enable accelerated networking (SR-IOV).",
    )
    public_ip_enabled: bool = False
    load_balancer_backend_pool_ids: list[str] = Field(
        default_factory=list,
        description="Existing Azure Load Balancer backend address pool ARM IDs to attach.",
    )
    load_balancer_inbound_nat_pool_ids: list[str] = Field(
        default_factory=list,
        description="Existing Azure Load Balancer inbound NAT pool ARM IDs to attach.",
    )
    application_gateway_backend_pool_ids: list[str] = Field(
        default_factory=list,
        description="Existing Application Gateway backend pool ARM IDs to attach.",
    )

    def to_arm_dict(self) -> dict[str, Any]:
        """Serialise to the ARM networkInterfaceConfiguration format."""
        ip_config_properties: dict[str, Any] = {"subnet": {"id": self.subnet_id}}
        if self.load_balancer_backend_pool_ids:
            ip_config_properties["loadBalancerBackendAddressPools"] = [
                {"id": pool_id} for pool_id in self.load_balancer_backend_pool_ids
            ]
        if self.load_balancer_inbound_nat_pool_ids:
            ip_config_properties["loadBalancerInboundNatPools"] = [
                {"id": pool_id} for pool_id in self.load_balancer_inbound_nat_pool_ids
            ]
        if self.application_gateway_backend_pool_ids:
            ip_config_properties["applicationGatewayBackendAddressPools"] = [
                {"id": pool_id} for pool_id in self.application_gateway_backend_pool_ids
            ]

        ip_config: dict[str, Any] = {
            "name": "ipconfig1",
            "properties": ip_config_properties,
        }
        if self.public_ip_enabled:
            ip_config["properties"]["publicIPAddressConfiguration"] = {
                "name": "publicip",
                "properties": {"deleteOption": "Delete"},
            }

        nic: dict[str, Any] = {
            "name": "nic-config",
            "properties": {
                "deleteOption": "Delete",
                "primary": True,
                "ipConfigurations": [ip_config],
            },
        }
        if self.accelerated_networking is not None:
            nic["properties"]["enableAcceleratedNetworking"] = self.accelerated_networking
        if self.network_security_group_id:
            nic["properties"]["networkSecurityGroup"] = {
                "id": self.network_security_group_id,
            }
        return nic


class AzureOSDiskConfig(ValueObject):
    """OS disk configuration for VMSS instances."""

    model_config = ConfigDict(populate_by_name=True)

    disk_size_gb: Optional[int] = None
    storage_account_type: AzureOSDiskType = AzureOSDiskType.PREMIUM_LRS
    caching: AzureCachingType = AzureCachingType.READ_WRITE
    ephemeral_os_disk: bool = False
    ephemeral_placement: Optional[str] = Field(
        default=None,
        validate_default=True,
        description="Placement for ephemeral OS disk: 'CacheDisk' or 'ResourceDisk'.",
    )

    @field_validator("ephemeral_placement", mode="before")
    @classmethod
    def _apply_ephemeral_defaults(
        cls, value: Optional[str], info: ValidationInfo
    ) -> Optional[str]:
        if value is None and info.data.get("ephemeral_os_disk"):
            return "CacheDisk"
        return value

    @model_validator(mode="after")
    def _validate_ephemeral(self) -> "AzureOSDiskConfig":
        if not self.ephemeral_os_disk and self.ephemeral_placement is not None:
            raise ValueError("ephemeral_placement requires ephemeral_os_disk=True")
        return self

    def to_arm_dict(self) -> dict[str, Any]:
        """Serialise to the ARM osDisk format."""
        disk: dict[str, Any] = {
            "createOption": "FromImage",
            "deleteOption": "Delete",
            "caching": self.caching.value,
            "managedDisk": {
                "storageAccountType": self.storage_account_type.value,
            },
        }
        if self.disk_size_gb:
            disk["diskSizeGB"] = self.disk_size_gb
        if self.ephemeral_os_disk:
            disk["diffDiskSettings"] = {
                "option": "Local",
                "placement": self.ephemeral_placement,
            }
        return disk


class AzureDataDisk(ValueObject):
    """A single data disk specification."""

    model_config = ConfigDict(populate_by_name=True)

    lun: int = Field(..., ge=0, le=63)
    disk_size_gb: int
    storage_account_type: AzureOSDiskType = AzureOSDiskType.PREMIUM_LRS
    caching: AzureCachingType = AzureCachingType.NONE

    def to_arm_dict(self) -> dict[str, Any]:
        """Serialise to the ARM dataDisk format."""
        return {
            "lun": self.lun,
            "createOption": "Empty",
            "deleteOption": "Delete",
            "diskSizeGB": self.disk_size_gb,
            "caching": self.caching.value,
            "managedDisk": {
                "storageAccountType": self.storage_account_type.value,
            },
        }
