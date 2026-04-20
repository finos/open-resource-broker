"""Azure Resource Manager.

Provides high-level resource management operations (tag, query, quota)
on top of the ``AzureClient``.
"""

from typing import Any, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.exceptions.azure_exceptions import (
    AzureInfrastructureError,
    TaggingError,
)
from orb.providers.azure.infrastructure.azure_client import AzureClient


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
            )
            self._logger.info(
                "Submitted tag update for resource %s with %s",
                resource_id,
                tags,
            )
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
            sku = vmss.sku
            orchestration_mode = vmss.orchestration_mode or "Flexible"
            provisioned_instance_count = self.get_vmss_member_count(
                resource_group=resource_group,
                vmss_name=vmss_name,
                orchestration_mode=str(orchestration_mode),
            )
            return {
                "vmss_name": vmss_name,
                "resource_group": resource_group,
                "capacity": sku.capacity if sku else 0,
                "vm_size": sku.name if sku else None,
                "provisioning_state": vmss.provisioning_state,
                "provisioned_instance_count": provisioned_instance_count,
            }
        except Exception as exc:
            self._logger.error(
                "Failed to get VMSS capacity for %s: %s", vmss_name, exc
            )
            raise AzureInfrastructureError(
                f"Failed to get VMSS capacity: {exc}"
            ) from exc

    def get_vmss_member_count(
        self,
        resource_group: str,
        vmss_name: str,
        orchestration_mode: Optional[str] = None,
    ) -> Optional[int]:
        """Return the attached instance count, or ``None`` when Azure cannot be queried."""
        resolved_orchestration_mode = orchestration_mode
        if resolved_orchestration_mode in (None, ""):
            vmss = self._azure_client.compute_client.virtual_machine_scale_sets.get(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
            )
            resolved_orchestration_mode = vmss.orchestration_mode or "Flexible"

        return self._get_vmss_instance_count(
            resource_group=resource_group,
            vmss_name=vmss_name,
            orchestration_mode=str(resolved_orchestration_mode),
        )

    def _get_vmss_instance_count(
        self, resource_group: str, vmss_name: str, orchestration_mode: str
    ) -> Optional[int]:
        """Return the attached instance count, or ``None`` when Azure cannot be queried."""
        compute = self._azure_client.compute_client

        try:
            if str(orchestration_mode).lower() == "flexible":
                count = 0
                for vm in compute.virtual_machines.list(resource_group_name=resource_group):
                    vmss_ref = vm.virtual_machine_scale_set
                    vmss_id = vmss_ref.id if vmss_ref else ""
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
            return None

    def vmss_exists(self, resource_group: str, vmss_name: str) -> Optional[bool]:
        """Return whether the VMSS still exists, or None if Azure could not be queried."""
        try:
            self._azure_client.compute_client.virtual_machine_scale_sets.get(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
            )
            return True
        except Exception as exc:
            # getattr: Exception subclasses vary — not all have error_code/status_code.
            error_code = getattr(exc, "error_code", None)
            if error_code in {"ResourceNotFound", "NotFound", "VMSSNotFoundError"}:
                return False

            status_code = getattr(exc, "status_code", None)
            if status_code == 404:
                return False

            message = str(exc).lower()
            if "not found" in message or "could not find" in message:
                return False

            self._logger.warning(
                "Failed to determine whether VMSS %s exists: %s", vmss_name, exc
            )
            return None

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
            self._azure_client.compute_client.virtual_machine_scale_sets.begin_create_or_update(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
                parameters=vmss,
            )
            self._logger.info(
                "Submitted VMSS '%s' scale to capacity %d",
                vmss_name,
                capacity,
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
                    "name": u.name.value,
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
