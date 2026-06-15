"""Azure template domain - VMSS-based template configuration."""

from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import (
    AzureAllocationStrategy,
    AzureCapacityReservationGroupId,
    AzureDiskEncryptionSetId,
    AzureEvictionPolicy,
    AzureLocationName,
    AzureOSDiskType,
    AzurePriority,
    AzureProximityPlacementGroupId,
    AzureProviderApi,
    AzureResourceGroupName,
    AzureSecurityType,
    AzureUpgradePolicyMode,
    AzureVmSizePreference,
    AzureVMSSOrchestrationMode,
)

__all__: list[str] = [
    "AzureAllocationStrategy",
    "AzureCapacityReservationGroupId",
    "AzureDiskEncryptionSetId",
    "AzureEvictionPolicy",
    "AzureLocationName",
    "AzureOSDiskType",
    "AzurePriority",
    "AzureProximityPlacementGroupId",
    "AzureProviderApi",
    "AzureResourceGroupName",
    "AzureSecurityType",
    "AzureTemplate",
    "AzureUpgradePolicyMode",
    "AzureVmSizePreference",
    "AzureVMSSOrchestrationMode",
]
