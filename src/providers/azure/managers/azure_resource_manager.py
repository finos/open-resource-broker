"""Azure Resource Manager.

Provides high-level resource management operations (tag, query, quota)
on top of the ``AzureClient``.
"""

from typing import Any, Optional

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from providers.azure.configuration.config import AzureProviderConfig
from providers.azure.exceptions.azure_exceptions import (
    AzureInfrastructureError,
    TaggingError,
)
from providers.azure.infrastructure.azure_client import AzureClient


@injectable
class AzureResourceManager:
    """High-level resource management for Azure."""

    def __init__(
        self,
        azure_client: AzureClient,
        config: AzureProviderConfig,
        logger: LoggingPort,
    ) -> None:
        self._azure_client = azure_client
        self._config = config
        self._logger = logger

    # ------------------------------------------------------------------
    # Tagging
    # ------------------------------------------------------------------

    def tag_resource(
        self, resource_id: str, tags: dict[str, str]
    ) -> None:
        """Apply tags to an ARM resource by its full resource ID.

        Uses the ``ResourceManagementClient.tags`` API which works on
        any ARM resource, regardless of type.
        """
        try:
            self._azure_client.resource_client.tags.begin_create_or_update_at_scope(
                scope=resource_id,
                parameters={"properties": {"tags": tags}},
            ).result()
            self._logger.info("Tagged resource %s with %s", resource_id, tags)
        except Exception as exc:
            raise TaggingError(
                f"Failed to tag resource {resource_id}: {exc}",
                resource_id=resource_id,
                tags=tags,
            ) from exc

    # ------------------------------------------------------------------
    # VMSS operations
    # ------------------------------------------------------------------

    def get_vmss_capacity(
        self, resource_group: str, vmss_name: str
    ) -> dict[str, Any]:
        """Return current VMSS capacity information."""
        try:
            vmss = self._azure_client.compute_client.virtual_machine_scale_sets.get(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
            )
            sku = getattr(vmss, "sku", None)
            orchestration_mode = getattr(vmss, "orchestration_mode", None) or "Flexible"
            provisioned_instance_count = self._get_vmss_instance_count(
                resource_group=resource_group,
                vmss_name=vmss_name,
                orchestration_mode=str(orchestration_mode),
            )
            return {
                "vmss_name": vmss_name,
                "resource_group": resource_group,
                "capacity": getattr(sku, "capacity", 0) if sku else 0,
                "vm_size": getattr(sku, "name", None) if sku else None,
                "provisioning_state": getattr(vmss, "provisioning_state", None),
                "provisioned_instance_count": provisioned_instance_count,
            }
        except Exception as exc:
            self._logger.error(
                "Failed to get VMSS capacity for %s: %s", vmss_name, exc
            )
            raise AzureInfrastructureError(
                f"Failed to get VMSS capacity: {exc}"
            ) from exc

    def _get_vmss_instance_count(
        self, resource_group: str, vmss_name: str, orchestration_mode: str
    ) -> int:
        """Return the number of provisioned instances currently attached to the VMSS."""
        compute = self._azure_client.compute_client

        try:
            if str(orchestration_mode).lower() == "flexible":
                count = 0
                for vm in compute.virtual_machines.list(resource_group_name=resource_group):
                    vmss_ref = getattr(vm, "virtual_machine_scale_set", None)
                    vmss_id = getattr(vmss_ref, "id", "") if vmss_ref else ""
                    if vmss_id and vmss_id.rstrip("/").endswith(f"/virtualMachineScaleSets/{vmss_name}"):
                        count += 1
                return count

            return sum(
                1
                for _ in compute.virtual_machine_scale_set_vms.list(
                    resource_group_name=resource_group,
                    virtual_machine_scale_set_name=vmss_name,
                )
            )
        except Exception as exc:
            self._logger.warning(
                "Failed to count VMSS instances for %s: %s", vmss_name, exc
            )
            return 0

    def scale_vmss(
        self, resource_group: str, vmss_name: str, capacity: int
    ) -> None:
        """Submit a VMSS SKU capacity update without waiting for completion."""
        try:
            self._logger.info(
                "Scaling VMSS '%s' to capacity %d", vmss_name, capacity
            )
            vmss = self._azure_client.compute_client.virtual_machine_scale_sets.get(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
            )
            vmss.sku.capacity = capacity
            poller = (
                self._azure_client.compute_client.virtual_machine_scale_sets.begin_create_or_update(
                    resource_group_name=resource_group,
                    vm_scale_set_name=vmss_name,
                    parameters=vmss,
                )
            )
            continuation_token = None
            if hasattr(poller, "continuation_token"):
                try:
                    continuation_token = poller.continuation_token()
                except Exception as exc:
                    self._logger.debug(
                        "Could not capture VMSS scale continuation token for '%s': %s",
                        vmss_name,
                        exc,
                    )
            self._logger.info(
                "Submitted VMSS '%s' scale to capacity %d (token=%s)",
                vmss_name,
                capacity,
                continuation_token,
            )
        except Exception as exc:
            self._logger.error(
                "Failed to scale VMSS '%s': %s", vmss_name, exc
            )
            raise AzureInfrastructureError(
                f"Failed to scale VMSS '{vmss_name}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Quota / subscription info
    # ------------------------------------------------------------------

    def get_compute_usage(
        self, location: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Return compute usage / quota information for a location."""
        loc = location or self._config.region
        try:
            usages = self._azure_client.compute_client.usage.list(location=loc)
            result: list[dict[str, Any]] = []
            for u in usages:
                result.append({
                    "name": getattr(u.name, "value", str(u.name)),
                    "current_value": u.current_value,
                    "limit": u.limit,
                    "unit": u.unit,
                })
            return result
        except Exception as exc:
            self._logger.error(
                "Failed to get compute usage for %s: %s", loc, exc
            )
            return []

    # ------------------------------------------------------------------
    # Resource group operations
    # ------------------------------------------------------------------

    def ensure_resource_group(
        self, resource_group: str, location: Optional[str] = None
    ) -> None:
        """Create the resource group if it does not exist."""
        loc = location or self._config.region
        try:
            self._azure_client.resource_client.resource_groups.create_or_update(
                resource_group_name=resource_group,
                parameters={"location": loc},
            )
            self._logger.info(
                "Ensured resource group '%s' exists in '%s'",
                resource_group,
                loc,
            )
        except Exception as exc:
            self._logger.error(
                "Failed to ensure resource group '%s': %s",
                resource_group,
                exc,
            )
            raise AzureInfrastructureError(
                f"Failed to create resource group '{resource_group}': {exc}"
            ) from exc
