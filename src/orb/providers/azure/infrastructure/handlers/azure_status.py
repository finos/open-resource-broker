"""Shared Azure VM status mapping.

Provides a single source of truth for mapping Azure VM power-state and
provisioning-state codes to ORB domain status strings.  Used by all
handlers that work with Azure SDK VM objects (SingleVM, VMSS) and by
the machine conversion service.
"""

from __future__ import annotations

from orb.providers.azure.infrastructure.sdk_shapes import AzureStatusWithCodeProtocol

# Azure VM state map.  PowerState/* entries are common to all
# VM-based handlers; ProvisioningState/* entries provide a fallback when
# a VM has not yet reached a power state (e.g. still being created).
AZURE_VM_STATE_MAP: dict[str, str] = {
    # Power states
    "PowerState/starting": "pending",
    "PowerState/running": "running",
    "PowerState/stopping": "stopping",
    "PowerState/stopped": "stopped",
    "PowerState/deallocating": "shutting-down",
    "PowerState/deallocated": "stopped",
    # Provisioning states (fallback when no PowerState is present)
    "ProvisioningState/creating": "pending",
    "ProvisioningState/succeeded": "running",
    "ProvisioningState/failed": "failed",
    "ProvisioningState/deleting": "shutting-down",
}


def resolve_power_state(statuses: list[AzureStatusWithCodeProtocol]) -> str:
    """Extract the ORB domain status from a list of Azure InstanceViewStatus objects.

    Tries PowerState/* first (most accurate for running VMs), then falls
    back to ProvisioningState/* for VMs that are still being created or
    torn down.
    """
    for status in statuses:
        code = str(status.code or "")
        if code.startswith("PowerState/"):
            return AZURE_VM_STATE_MAP.get(code, "unknown")
    # Fallback to provisioning state
    for status in statuses:
        code = str(status.code or "")
        if code.startswith("ProvisioningState/"):
            return AZURE_VM_STATE_MAP.get(code, "unknown")
    return "unknown"
