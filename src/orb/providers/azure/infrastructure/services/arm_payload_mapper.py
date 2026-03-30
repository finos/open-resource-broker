"""ARM payload mapper.

Converts ``AzureTemplate`` domain objects into Azure Resource Manager (ARM)
payloads for VMSS and SingleVM resources.  This keeps infrastructure
serialisation concerns (API versions, ARM resource type strings, payload
structure) out of the domain aggregate.
"""

from __future__ import annotations

from typing import Any, Optional

from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import (
    AzureCachingType,
    AzureOSDiskType,
    AzurePriority,
    AzureVMSSOrchestrationMode,
)

# Flexible VMSS network profiles require a Microsoft.Network API version
# in the payload.
_VMSS_FLEX_NETWORK_API_VERSION = "2022-11-01"


class ArmPayloadMapper:
    """Stateless mapper from ``AzureTemplate`` to ARM resource payloads."""

    # ------------------------------------------------------------------
    # VMSS payload
    # ------------------------------------------------------------------

    @staticmethod
    def vmss_payload(template: AzureTemplate) -> dict[str, Any]:
        """Build the ARM resource payload for a VMSS create/update.

        Equivalent to the former ``AzureTemplate.to_azure_api_format()``.
        """
        properties: dict[str, Any] = {
            "orchestrationMode": template.orchestration_mode.value,
            "singlePlacementGroup": template.single_placement_group,
            "upgradePolicy": {"mode": template.upgrade_policy_mode.value},
        }

        if template.orchestration_mode == AzureVMSSOrchestrationMode.UNIFORM:
            properties["overprovision"] = template.overprovision

        platform_fault_domain_count = template.platform_fault_domain_count
        if template.orchestration_mode == AzureVMSSOrchestrationMode.FLEXIBLE:
            platform_fault_domain_count = platform_fault_domain_count or 1
        if platform_fault_domain_count is not None:
            properties["platformFaultDomainCount"] = platform_fault_domain_count

        # --- Virtual machine profile ---
        vm_profile: dict[str, Any] = {"hardwareProfile": {}}

        storage_profile = _build_storage_profile(template)
        vm_profile["storageProfile"] = storage_profile

        # Network profile
        if template.network_config:
            nic_config = template.network_config.to_arm_dict()
            vm_profile["networkProfile"] = {
                "networkInterfaceConfigurations": [nic_config],
                "networkApiVersion": _VMSS_FLEX_NETWORK_API_VERSION,
            }

        # OS profile
        os_profile: dict[str, Any] = {
            "computerNamePrefix": (template.vmss_name or template.template_id)[:9],
            "adminUsername": template.admin_username,
        }
        if template.custom_data:
            os_profile["customData"] = template.custom_data
        if template.ssh_public_keys:
            os_profile["linuxConfiguration"] = _build_linux_ssh_config(template)
        vm_profile["osProfile"] = os_profile

        _apply_priority(template, vm_profile)
        _apply_security_profile(template, vm_profile)

        # Extension profile
        if template.extension_profile:
            vm_profile["extensionProfile"] = {
                "extensions": template.extension_profile,
            }

        properties["virtualMachineProfile"] = vm_profile

        # --- Spot restore policy ---
        if template.spot_restore_enabled:
            spot_restore: dict[str, Any] = {"enabled": True}
            if template.spot_restore_timeout:
                spot_restore["restoreTimeout"] = template.spot_restore_timeout
            properties["spotRestorePolicy"] = spot_restore

        if template.spot_percentage is not None:
            properties["priorityMixPolicy"] = {
                "baseRegularPriorityCount": template.base_regular_priority_count,
                "regularPriorityPercentageAboveBase": 100 - template.spot_percentage,
            }

        # --- Identity ---
        identity = _build_identity(template)

        # --- Top-level resource ---
        resource: dict[str, Any] = {
            "type": "Microsoft.Compute/virtualMachineScaleSets",
            "name": template.vmss_name or f"vmss-{template.template_id}",
            "location": template.location.value,
            "sku": {
                "name": "Mix" if template.vm_sizes else template.vm_size,
                "capacity": template.max_instances,
            },
            "properties": properties,
            "tags": template.tags if template.tags else {},
        }

        if template.vm_sizes:
            sku_profile: dict[str, Any] = {
                "vmSizes": [
                    {"name": vm_size}
                    for vm_size in [template.vm_size, *template.vm_sizes]
                ]
            }
            if template.spot_allocation_strategy:
                sku_profile["allocationStrategy"] = template.spot_allocation_strategy.to_arm_value()
            resource["skuProfile"] = sku_profile

        if template.zones:
            resource["zones"] = template.zones
        if identity:
            resource["identity"] = identity
        if template.proximity_placement_group_id:
            properties["proximityPlacementGroup"] = {
                "id": template.proximity_placement_group_id.value,
            }
        if template.capacity_reservation_group_id:
            vm_profile["capacityReservation"] = {
                "capacityReservationGroup": {
                    "id": template.capacity_reservation_group_id.value,
                },
            }
        if template.node_attributes:
            properties.update(template.node_attributes)

        return resource

    # ------------------------------------------------------------------
    # SingleVM payload
    # ------------------------------------------------------------------

    @staticmethod
    def single_vm_payload(
        template: AzureTemplate,
        vm_name: str,
        nic_id: str,
        *,
        vm_size_override: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build ARM VM create parameters for a single VM deployment.

        Equivalent to the former ``SingleVMHandler._build_vm_params()``.
        """
        params: dict[str, Any] = {
            "location": template.location.value,
            "properties": {
                "hardwareProfile": {"vmSize": vm_size_override or template.vm_size},
                "storageProfile": _build_storage_profile(
                    template,
                    default_os_disk_storage_account_type=AzureOSDiskType.STANDARD_LRS.value,
                    include_default_os_disk_caching=False,
                ),
                "osProfile": {
                    "computerName": vm_name[:15],  # Azure host name limit
                    "adminUsername": template.admin_username,
                },
                "networkProfile": {
                    "networkInterfaces": [
                        {
                            "id": nic_id,
                            "properties": {
                                "primary": True,
                                "deleteOption": "Delete",
                            },
                        }
                    ]
                },
            },
            "tags": template.tags or {},
        }

        # SSH keys
        if template.ssh_public_keys:
            params["properties"]["osProfile"]["linuxConfiguration"] = (
                _build_linux_ssh_config(template)
            )

        # Custom data
        if template.custom_data:
            params["properties"]["osProfile"]["customData"] = template.custom_data

        _apply_priority(template, params["properties"])

        _apply_security_profile(template, params["properties"])

        # Capacity reservation
        if template.capacity_reservation_group_id:
            params["properties"]["capacityReservation"] = {
                "capacityReservationGroup": {
                    "id": template.capacity_reservation_group_id.value,
                }
            }

        # Identity
        identity = _build_identity(template)
        if identity:
            params["identity"] = identity

        # Availability zones
        if template.zones:
            params["zones"] = template.zones

        return params


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------

def _build_storage_profile(
    template: AzureTemplate,
    *,
    default_os_disk_storage_account_type: str = AzureOSDiskType.PREMIUM_LRS.value,
    include_default_os_disk_caching: bool = True,
) -> dict[str, Any]:
    """Build the storageProfile section common to Azure compute payloads."""
    storage_profile: dict[str, Any] = {}

    if template.image:
        storage_profile["imageReference"] = template.image.to_arm_dict()
    elif template.image_id:
        storage_profile["imageReference"] = {"id": template.image_id}

    if template.os_disk:
        storage_profile["osDisk"] = template.os_disk.to_arm_dict()
    else:
        default_os_disk: dict[str, Any] = {
            "createOption": "FromImage",
            "deleteOption": "Delete",
            "managedDisk": {
                "storageAccountType": default_os_disk_storage_account_type,
            },
        }
        if include_default_os_disk_caching:
            default_os_disk["caching"] = AzureCachingType.READ_WRITE.value
        storage_profile["osDisk"] = default_os_disk

    if template.data_disks:
        storage_profile["dataDisks"] = [d.to_arm_dict() for d in template.data_disks]

    _apply_disk_encryption(template, storage_profile)

    return storage_profile


def _apply_disk_encryption(template: AzureTemplate, storage_profile: dict[str, Any]) -> None:
    """Apply disk encryption set to OS and data disks if configured."""
    if not template.disk_encryption_set_id:
        return

    os_disk_managed = storage_profile.get("osDisk", {}).get("managedDisk")
    if isinstance(os_disk_managed, dict):
        os_disk_managed["diskEncryptionSet"] = {
            "id": template.disk_encryption_set_id.value,
        }

    for data_disk in storage_profile.get("dataDisks", []):
        managed_disk = data_disk.get("managedDisk")
        if isinstance(managed_disk, dict):
            managed_disk["diskEncryptionSet"] = {
                "id": template.disk_encryption_set_id.value,
            }


def _build_linux_ssh_config(template: AzureTemplate) -> dict[str, Any]:
    """Build the linuxConfiguration SSH block."""
    return {
        "disablePasswordAuthentication": True,
        "ssh": {
            "publicKeys": [
                {
                    "path": f"/home/{template.admin_username}/.ssh/authorized_keys",
                    "keyData": key,
                }
                for key in template.ssh_public_keys
            ],
        },
    }


def _apply_priority(template: AzureTemplate, target: dict[str, Any]) -> None:
    """Apply priority / spot / billing profile to a VM profile dict."""
    if template.priority == AzurePriority.REGULAR:
        return
    target["priority"] = template.priority.value
    if template.eviction_policy:
        target["evictionPolicy"] = template.eviction_policy.value
    if template.billing_profile_max_price is not None:
        target["billingProfile"] = {
            "maxPrice": template.billing_profile_max_price,
        }


def _apply_security_profile(template: AzureTemplate, target: dict[str, Any]) -> None:
    """Apply security profile (TrustedLaunch, Confidential, encryption at host)."""
    if not template.security_type:
        return
    security_profile: dict[str, Any] = {
        "securityType": template.security_type.value,
    }
    uefi: dict[str, Any] = {}
    if template.secure_boot_enabled is not None:
        uefi["secureBootEnabled"] = template.secure_boot_enabled
    if template.vtpm_enabled is not None:
        uefi["vTpmEnabled"] = template.vtpm_enabled
    if uefi:
        security_profile["uefiSettings"] = uefi
    if template.encryption_at_host is not None:
        security_profile["encryptionAtHost"] = template.encryption_at_host
    target["securityProfile"] = security_profile


def _build_identity(template: AzureTemplate) -> dict[str, Any] | None:
    """Build the identity block for the ARM resource."""
    if template.system_assigned_identity and template.user_assigned_identity_ids:
        return {
            "type": "SystemAssigned, UserAssigned",
            "userAssignedIdentities": {
                uid: {} for uid in template.user_assigned_identity_ids
            },
        }
    if template.system_assigned_identity:
        return {"type": "SystemAssigned"}
    if template.user_assigned_identity_ids:
        return {
            "type": "UserAssigned",
            "userAssignedIdentities": {
                uid: {} for uid in template.user_assigned_identity_ids
            },
        }
    return None
