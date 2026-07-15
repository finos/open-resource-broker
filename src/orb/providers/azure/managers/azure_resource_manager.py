"""Azure Resource Manager.

Provides the live async Azure resource-management helpers used by ORB.
"""

from typing import Optional

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.di.injectable import injectable
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.exceptions.azure_exceptions import AzureInfrastructureError
from orb.providers.azure.infrastructure.azure_client import AzureClient
from orb.providers.azure.services.resource_metadata_service import VmssCapacityInfo


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
    # VMSS operations
    # ------------------------------------------------------------------

    async def get_vmss_capacity_async(
        self, resource_group: str, vmss_name: str
    ) -> VmssCapacityInfo:
        """Return current VMSS capacity information via the async Azure SDK."""
        try:
            compute = await self._azure_client.get_async_compute_client()
            vmss = await compute.virtual_machine_scale_sets.get(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
            )
            sku = vmss.sku
            orchestration_mode = vmss.orchestration_mode or "Flexible"
            provisioned_instance_count = await self.get_vmss_member_count_async(
                resource_group=resource_group,
                vmss_name=vmss_name,
                orchestration_mode=str(orchestration_mode),
            )
            return {
                "vmss_name": vmss_name,
                "resource_group": resource_group,
                "capacity": int(sku.capacity or 0) if sku else 0,
                "vm_size": sku.name if sku else None,
                "provisioning_state": vmss.provisioning_state,
                "provisioned_instance_count": provisioned_instance_count or 0,
            }
        except Exception as exc:
            self._logger.error("Failed to get VMSS capacity for %s: %s", vmss_name, exc)
            raise AzureInfrastructureError(f"Failed to get VMSS capacity: {exc}") from exc

    async def get_vmss_member_count_async(
        self,
        resource_group: str,
        vmss_name: str,
        orchestration_mode: Optional[str] = None,
    ) -> Optional[int]:
        """Return the attached instance count via the async Azure SDK."""
        resolved_orchestration_mode = orchestration_mode
        if resolved_orchestration_mode in (None, ""):
            compute = await self._azure_client.get_async_compute_client()
            vmss = await compute.virtual_machine_scale_sets.get(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
            )
            resolved_orchestration_mode = vmss.orchestration_mode or "Flexible"

        return await self._get_vmss_instance_count_async(
            resource_group=resource_group,
            vmss_name=vmss_name,
            orchestration_mode=str(resolved_orchestration_mode),
        )

    async def _get_vmss_instance_count_async(
        self, resource_group: str, vmss_name: str, orchestration_mode: str
    ) -> Optional[int]:
        """Return the attached instance count via the async Azure SDK."""
        compute = await self._azure_client.get_async_compute_client()

        try:
            if str(orchestration_mode).lower() == "flexible":
                count = 0
                pager = compute.virtual_machines.list(resource_group_name=resource_group)
                async for vm in pager:
                    vmss_ref = vm.virtual_machine_scale_set
                    vmss_id = vmss_ref.id if vmss_ref else ""
                    if vmss_id and vmss_id.rstrip("/").endswith(
                        f"/virtualMachineScaleSets/{vmss_name}"
                    ):
                        count += 1
                return count

            count = 0
            pager = compute.virtual_machine_scale_set_vms.list(
                resource_group_name=resource_group,
                virtual_machine_scale_set_name=vmss_name,
            )
            async for _ in pager:
                count += 1
            return count
        except Exception as exc:
            self._logger.warning("Failed to count VMSS instances for %s: %s", vmss_name, exc)
            return None

    @staticmethod
    def _vmss_lookup_not_found(exc: Exception) -> bool:
        """Return whether a VMSS lookup exception represents a not-found result."""
        # getattr: Exception subclasses vary — not all have error_code/status_code.
        error_code = getattr(exc, "error_code", None)
        if error_code in {"ResourceNotFound", "NotFound", "VMSSNotFoundError"}:
            return True

        status_code = getattr(exc, "status_code", None)
        if status_code == 404:
            return True

        message = str(exc).lower()
        return "not found" in message or "could not find" in message

    async def vmss_exists_async(self, resource_group: str, vmss_name: str) -> Optional[bool]:
        """Return whether the VMSS still exists, or None if Azure could not be queried."""
        try:
            compute = await self._azure_client.get_async_compute_client()
            await compute.virtual_machine_scale_sets.get(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
            )
            return True
        except Exception as exc:
            if self._vmss_lookup_not_found(exc):
                return False

            self._logger.warning("Failed to determine whether VMSS %s exists: %s", vmss_name, exc)
            return None
