"""Azure configuration provider - single source of truth."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from infrastructure.interfaces.provider import BaseProviderConfig


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class AzureOrchestrationType(str, Enum):
    """VMSS orchestration mode."""

    FLEXIBLE = "Flexible"
    UNIFORM = "Uniform"


class AzureEvictionPolicy(str, Enum):
    """Spot VM eviction policy."""

    DEALLOCATE = "Deallocate"
    DELETE = "Delete"


class AzureVMPriority(str, Enum):
    """VM priority tier."""

    REGULAR = "Regular"
    SPOT = "Spot"
    LOW_PRIORITY = "LowPriority"


class AzureStorageAccountType(str, Enum):
    """Managed-disk storage account types."""

    PREMIUM_LRS = "Premium_LRS"
    PREMIUM_ZRS = "Premium_ZRS"
    STANDARD_LRS = "Standard_LRS"
    STANDARD_SSD_LRS = "StandardSSD_LRS"
    ULTRA_SSD_LRS = "UltraSSD_LRS"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class HandlerCapabilityConfig(BaseModel):
    """Advertised handler capabilities for this provider instance."""

    vmss: bool = Field(True, description="VMSS handler available")
    fleet: bool = Field(True, description="Azure Compute Fleet handler available")


class HandlerDefaultsConfig(BaseModel):
    """Default handler selection."""

    default_handler: str = Field(
        "VMSS",
        description="Handler to use when template does not specify one ('VMSS' or 'AzureFleet')",
    )


class HandlersConfig(BaseModel):
    """Aggregated handler configuration."""

    capabilities: HandlerCapabilityConfig = Field(
        default_factory=HandlerCapabilityConfig
    )
    defaults: HandlerDefaultsConfig = Field(
        default_factory=HandlerDefaultsConfig
    )


class VMSSConfiguration(BaseModel):
    """Default VMSS creation parameters applied when the template does not
    override a field."""

    orchestration_mode: AzureOrchestrationType = Field(
        AzureOrchestrationType.FLEXIBLE,
        description="VMSS orchestration mode (Flexible recommended for new workloads)",
    )
    platform_fault_domain_count: int = Field(
        1,
        ge=1,
        le=5,
        description="Number of fault domains for the scale set",
    )
    single_placement_group: bool = Field(
        False,
        description="Restrict the scale set to a single placement group (Uniform only)",
    )
    os_disk_type: AzureStorageAccountType = Field(
        AzureStorageAccountType.PREMIUM_LRS,
        description="Default managed-disk type for OS disks",
    )
    os_disk_size_gb: Optional[int] = Field(
        None,
        ge=1,
        description="Override OS disk size in GiB (None = use image default)",
    )
    enable_accelerated_networking: bool = Field(
        True,
        description="Enable accelerated networking on NICs where supported",
    )


# ---------------------------------------------------------------------------
# CycleCloud provider models
# ---------------------------------------------------------------------------

class CycleCloudSSHConfig(BaseModel):
    """SSH connectivity configuration for CycleCloud server."""

    host: Optional[str] = Field(None, description="CycleCloud SSH host or IP")
    user: Optional[str] = Field(None, description="SSH username")
    port: int = Field(22, description="SSH port")
    key_path: Optional[str] = Field(None, description="Path to private key file")


class CycleCloudConfig(BaseModel):
    """CycleCloud connection configuration."""

    url: Optional[str] = Field(None, description="CycleCloud REST API base URL")
    credential_path: Optional[str] = Field(
        None,
        description="Path to a JSON file containing CycleCloud credentials and optional auth overrides",
    )
    username: Optional[str] = Field(None, description="CycleCloud API username")
    password: Optional[str] = Field(None, description="CycleCloud API password")
    verify_ssl: bool = Field(True, description="Verify CycleCloud TLS certs")
    ssh: Optional[CycleCloudSSHConfig] = Field(None, description="Optional SSH connection settings")


# ---------------------------------------------------------------------------
# Root provider config
# ---------------------------------------------------------------------------

class AzureProviderConfig(BaseProviderConfig):
    """Configuration for the Azure provider (VMSS / Compute Fleet)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    # ------------------------------------------------------------------
    # Provider identity
    # ------------------------------------------------------------------
    provider_type: str = Field("azure", description="Provider type identifier")
    region: str = Field("eastus2", description="Azure region / location slug")

    # ------------------------------------------------------------------
    # Azure subscription & resource targeting
    # ------------------------------------------------------------------
    subscription_id: Optional[str] = Field(
        None, description="Azure subscription ID (UUID)"
    )
    resource_group: Optional[str] = Field(
        None,
        description="Default resource group for created resources (1-90 chars)",
    )
    vnet_resource_group: Optional[str] = Field(
        None,
        description="Resource group containing the VNet/subnets (if different from resource_group)",
    )
    vnet_name: Optional[str] = Field(
        None, description="Virtual network name used when resolving subnet names"
    )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    client_id: Optional[str] = Field(
        None,
        description="Managed-identity client ID for user-assigned identity selection",
    )

    # ------------------------------------------------------------------
    # Retry / timeout
    # ------------------------------------------------------------------
    max_retries: int = Field(
        3, ge=0, description="Maximum SDK retry attempts for transient errors"
    )
    connect_timeout: int = Field(
        30, ge=1, description="Connection timeout for ARM API calls in seconds"
    )
    read_timeout: int = Field(
        60, ge=1, description="Read timeout for ARM API calls in seconds"
    )
    instance_pending_timeout_sec: int = Field(
        300, ge=0, description="How long to wait for VMSS instances to reach Running state"
    )
    polling_interval_sec: int = Field(
        15, ge=1, description="Seconds between status-poll iterations"
    )

    # ------------------------------------------------------------------
    # VMSS defaults
    # ------------------------------------------------------------------
    vmss: VMSSConfiguration = Field(
        default_factory=VMSSConfiguration,
        description="Default VMSS creation parameters",
    )

    # ------------------------------------------------------------------
    # Spot / priority defaults
    # ------------------------------------------------------------------
    default_vm_priority: AzureVMPriority = Field(
        AzureVMPriority.REGULAR,
        description="Default VM priority when the template does not specify one",
    )
    default_eviction_policy: AzureEvictionPolicy = Field(
        AzureEvictionPolicy.DELETE,
        description="Default Spot eviction policy",
    )
    spot_max_price: float = Field(
        -1.0,
        description="Default maximum Spot price in USD/hr (-1 = pay up to on-demand price)",
    )

    # ------------------------------------------------------------------
    # Capacity / placement
    # ------------------------------------------------------------------
    proximity_placement_group_id: Optional[str] = Field(
        None,
        description="ARM resource ID of a Proximity Placement Group to associate by default",
    )
    capacity_reservation_group_id: Optional[str] = Field(
        None,
        description="ARM resource ID of a Capacity Reservation Group to use by default",
    )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------
    handlers: HandlersConfig = Field(default_factory=HandlersConfig)

    # ------------------------------------------------------------------
    # CycleCloud
    # ------------------------------------------------------------------
    cyclecloud: Optional[CycleCloudConfig] = Field(
        default=None,
        description="CycleCloud integration configuration (URL, credentials, SSH)",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("resource_group")
    @classmethod
    def validate_resource_group(cls, v: Optional[str]) -> Optional[str]:
        """Enforce ARM resource-group naming rules."""
        if v is None:
            return v
        if not (1 <= len(v) <= 90):
            raise ValueError("resource_group must be 1-90 characters")
        import re
        if not re.match(r"^[a-zA-Z0-9_\-.()\[\]]+$", v):
            raise ValueError(
                "resource_group contains invalid characters "
                "(allowed: alphanumeric, _, -, ., (, ), [, ])"
            )
        return v

    @field_validator("subscription_id")
    @classmethod
    def validate_subscription_id(cls, v: Optional[str]) -> Optional[str]:
        """Validate UUID-like subscription ID format."""
        if v is None:
            return v
        import re
        if not re.match(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            v,
        ):
            raise ValueError(
                "subscription_id must be a valid UUID "
                "(xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)"
            )
        return v
