"""Azure domain layer."""

from orb.providers.azure.domain.template import (
    AzureAllocationStrategy,
    AzureCapacityReservationGroupId,
    AzureDiskEncryptionSetId,
    AzureEvictionPolicy,
    AzureLocationName,
    AzureOSDiskType,
    AzurePriority,
    AzureProviderApi,
    AzureProximityPlacementGroupId,
    AzureResourceGroupName,
    AzureSecurityType,
    AzureTemplate,
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
