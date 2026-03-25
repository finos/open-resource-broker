"""VMSS Handler - provisions VM Scale Sets via the Azure Compute SDK.

This is the primary handler for the Azure provider

It handles:
- Creating a VMSS with Flexible orchestration (default) or Uniform
- Listing instances in a VMSS for status checks
- Deleting VMSS instances and the scale set itself

Important limitation:
- Azure Flexible VMSS does not expose an AWS-ASG-style "detach these exact
  instances and decrement desired capacity for them" flow.
- Scaling in first can let Azure choose different victims than the caller
  requested, so Flexible VMSS termination in this provider submits explicit VM
  deletes first and reconciles VMSS capacity later.
- That deferred reconciliation is currently tracked in process memory by the
  Azure strategy. This is intentional current behavior: a narrow best-effort
  operational follow-up, not a durable workflow guarantee across process
  restarts.
- If we need durable reconciliation later, treat that as explicit future work
  and persist only the minimal reconciliation reference needed to resume it.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.request.aggregate import Request
from orb.infrastructure.di.container import get_container
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions.azure_exceptions import (
    AzureValidationError,
    QuotaExceededError,
    TerminationError,
    VMSSCreationError,
    VMSSNotFoundError,
)
from orb.providers.azure.infrastructure.error_utils import (
    canonical_azure_error_code,
    extract_azure_error_details,
)
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureHandler
from orb.providers.azure.domain.template.value_objects import AzureVMSSOrchestrationMode


# Azure VM power-state → domain status mapping
_AZURE_STATE_MAP: dict[str, str] = {
    "PowerState/starting": "pending",
    "PowerState/running": "running",
    "PowerState/stopping": "stopping",
    "PowerState/stopped": "stopped",
    "PowerState/deallocating": "shutting-down",
    "PowerState/deallocated": "stopped",
    "ProvisioningState/creating": "pending",
    "ProvisioningState/succeeded": "running",
    "ProvisioningState/failed": "failed",
    "ProvisioningState/deleting": "shutting-down",
}


def _resolve_power_state(statuses: list[Any]) -> str:
    """Extract the domain status from a list of Azure InstanceViewStatus objects."""
    for status in statuses:
        code = status.code if hasattr(status, "code") else str(status.get("code", ""))
        if code.startswith("PowerState/"):
            return _AZURE_STATE_MAP.get(code, "unknown")
    # Fallback to provisioning state
    for status in statuses:
        code = status.code if hasattr(status, "code") else str(status.get("code", ""))
        if code.startswith("ProvisioningState/"):
            return _AZURE_STATE_MAP.get(code, "unknown")
    return "unknown"


def _status_attr(status: Any, attr: str, default: Any = None) -> Any:
    """Read Azure status attributes from SDK objects or attribute-based test doubles."""
    if hasattr(status, attr):
        return getattr(status, attr)
    return default


def _build_vmss_delete_instance_ids(instance_ids: list[str]) -> Any:
    """Build the VMSS delete payload using the SDK model when available."""
    try:
        from azure.mgmt.compute.models import VirtualMachineScaleSetVMInstanceRequiredIDs
    except ImportError:
        return {"instance_ids": instance_ids}
    return VirtualMachineScaleSetVMInstanceRequiredIDs(instance_ids=instance_ids)


@injectable
class VMSSHandler(AzureHandler):
    """Handler that creates and manages VMSS resources.

    ``provider_api = "VMSS"`` or ``"VMSSUniform"``
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        container = get_container()
        try:
            from orb.providers.azure.infrastructure.services.azure_native_spec_service import (
                AzureNativeSpecService,
            )

            self.azure_native_spec_service = container.get(AzureNativeSpecService)
        except Exception:
            self.azure_native_spec_service = None
        try:
            from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager

            self.azure_resource_manager = container.get(AzureResourceManager)
        except Exception:
            self.azure_resource_manager = None

    # ------------------------------------------------------------------
    # acquire_hosts
    # ------------------------------------------------------------------

    def acquire_hosts(
        self, request: Request, template: AzureTemplate
    ) -> dict[str, Any]:
        """Create a VMSS and return the result.

        The VMSS is created using the ARM payload generated by
        ``AzureTemplate.to_azure_api_format()``. The create LRO is submitted
        and the VMSS name is returned immediately; instance discovery happens
        later via status checks.
        """
        vmss_name = template.vmss_name or f"vmss-{template.template_id}-{uuid.uuid4().hex[:8]}"
        resource_group = template.resource_group
        location = template.location

        self._logger.info(
            "Creating VMSS '%s' in resource group '%s' (location=%s, capacity=%d)",
            vmss_name,
            resource_group,
            location,
            request.requested_count,
        )

        try:
            # Resolve subnet_id if network_config is missing
            if not template.network_config:
                subnet_id = None
                if template.subnet_ids:
                    candidate = template.subnet_ids[0]
                    if candidate and candidate != "default-subnet":
                        subnet_id = candidate

                if subnet_id:
                    # Auto-create network_config from subnet_id
                    from orb.providers.azure.domain.template.value_objects import (
                        AzureNetworkConfig,
                    )

                    template.network_config = AzureNetworkConfig(subnet_id=subnet_id)
                    self._logger.debug("Auto-created network_config from subnet_id: %s", subnet_id)
                else:
                    raise ValueError(
                        "No subnet specified. Add 'subnet_id' (full ARM resource ID) "
                        "to the template in subnet_ids or network_config, e.g.: "
                        "/subscriptions/<sub>/resourceGroups/<rg>/providers/"
                        "Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>"
                    )

            arm_payload = template.to_azure_api_format()

            if self.azure_native_spec_service:
                merged_payload = self.azure_native_spec_service.process_provider_api_spec_with_merge(
                    template=template,
                    request=request,
                    default_payload=arm_payload,
                    extra_context={"vmss_name": vmss_name},
                )
                if merged_payload:
                    arm_payload = merged_payload

            # Override capacity with the requested count
            arm_payload.setdefault("sku", {})
            arm_payload["sku"]["capacity"] = request.requested_count
            arm_payload["name"] = vmss_name

            compute = self.azure_client.compute_client

            # Resolve ssh_key_name → actual key data if needed. Must
            # happen after to_azure_api_format() has been called so
            # the resolved keys end up in the ARM payload's osProfile.
            if template.ssh_key_name and not template.ssh_public_keys:
                resolved_keys = template.resolve_ssh_keys(compute)
                # Patch the already-built ARM payload with the resolved keys
                vm_profile = arm_payload["properties"]["virtualMachineProfile"]
                vm_profile["osProfile"]["linuxConfiguration"] = {
                    "disablePasswordAuthentication": True,
                    "ssh": {
                        "publicKeys": [
                            {
                                "path": f"/home/{template.admin_username}/.ssh/authorized_keys",
                                "keyData": key,
                            }
                            for key in resolved_keys
                        ],
                    },
                }

            compute.virtual_machine_scale_sets.begin_create_or_update(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
                parameters=arm_payload,
            )

            self._logger.info(
                "Submitted native VMSS create for '%s'; tracking will continue via status checks",
                vmss_name,
            )

            resource_ids = [vmss_name]
            return {
                "success": True,
                "resource_ids": resource_ids,
                "instances": [],
                "error_message": None,
                "provider_data": {
                    "vmss_name": vmss_name,
                    "resource_group": resource_group,
                    "location": location,
                    "provisioning_state": "creating",
                    "operation_status": "submitted",
                    # Azure async create returns resource tracking first and instances later.
                    # Mark the submit attempt as final so generic top-up retry logic does not
                    # reissue the same create request and duplicate resources.
                    "fulfillment_final": True,
                },
            }

        except Exception as exc:
            error_msg = f"Failed to create VMSS '{vmss_name}': {exc}"
            self._logger.error(error_msg)

            # Translate Azure SDK errors into domain exceptions where possible
            error_code = canonical_azure_error_code(exc)
            error_str = extract_azure_error_details(exc)["message"].lower()
            if error_code in {"QuotaExceeded", "OperationNotAllowed", "ResourceQuotaExceeded"} or (
                "quota" in error_str or "exceeded" in error_str
            ):
                raise QuotaExceededError(error_msg) from exc
            if error_code in {"InvalidRequest", "InvalidParameter", "BadRequest"} or (
                "validation" in error_str or "invalid" in error_str
            ):
                raise AzureValidationError(error_msg) from exc

            raise VMSSCreationError(
                message=error_msg,
                template_id=template.template_id,
                vmss_name=vmss_name,
            ) from exc

    # ------------------------------------------------------------------
    # check_hosts_status
    # ------------------------------------------------------------------

    def check_hosts_status(self, request: Request) -> list[dict[str, Any]]:
        """Return instance details for every VM in the VMSS(es)."""
        resource_ids: list[str] = getattr(request, "resource_ids", []) or []
        if not resource_ids:
            self._logger.warning("check_hosts_status called with no resource_ids")
            return []

        all_instances: list[dict[str, Any]] = []
        status_errors: list[str] = []
        fail_on_partial_status_error = bool(
            (request.metadata or {}).get("fail_on_partial_status_error", False)
        )

        for vmss_name in resource_ids:
            # We need the resource group.  Convention: stored on request metadata
            # or fall back to the azure_client default.
            resource_group = (
                (request.metadata or {}).get("resource_group")
                or self.azure_client.resource_group
            )
            if not resource_group:
                self._logger.error(
                    "Cannot resolve resource_group for VMSS '%s'", vmss_name
                )
                continue

            try:
                instances = self._list_vmss_instances(
                    resource_group, vmss_name, include_instance_view=True
                )
                all_instances.extend(instances)
            except Exception as exc:
                error_message = f"Failed to list instances for VMSS '{vmss_name}': {exc}"
                self._logger.error(error_message)
                status_errors.append(error_message)

        if fail_on_partial_status_error and status_errors:
            raise RuntimeError("; ".join(status_errors))

        return all_instances

    # ------------------------------------------------------------------
    # release_hosts
    # ------------------------------------------------------------------

    def release_hosts(
        self,
        machine_ids: list[str],
        resource_id: str,
        context: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Delete specific VM instances from a VMSS, or the entire VMSS."""
        context = context or {}
        resource_group = (
            context.get("resource_group") or self.azure_client.resource_group
        )
        if not resource_group:
            raise TerminationError(
                "resource_group is required for release_hosts",
                resource_ids=machine_ids,
            )

        vmss_name = resource_id
        compute = self.azure_client.compute_client
        orchestration_mode = self._get_vmss_orchestration_mode(resource_group, vmss_name)

        delete_vmss = context.get("delete_vmss", False)

        if delete_vmss:
            if orchestration_mode == AzureVMSSOrchestrationMode.FLEXIBLE:
                attached_vms = self._list_vmss_instances(
                    resource_group, vmss_name, include_instance_view=False
                )
                for vm in attached_vms:
                    vm_name = vm.get("instance_id")
                    if not vm_name:
                        continue
                    poller = compute.virtual_machines.begin_delete(
                        resource_group_name=resource_group,
                        vm_name=vm_name,
                    )
            # Delete the entire VMSS
            self._logger.info(
                "Deleting entire VMSS '%s' in resource group '%s'",
                vmss_name,
                resource_group,
            )
            try:
                poller = compute.virtual_machine_scale_sets.begin_delete(
                    resource_group_name=resource_group,
                    vm_scale_set_name=vmss_name,
                )
                self._logger.info("Submitted delete for VMSS '%s'", vmss_name)
                return {
                    "provider_data": {
                        "resource_group": resource_group,
                        "vmss_name": vmss_name,
                        "operation_status": "submitted",
                        "delete_vmss": True,
                    }
                }
            except Exception as exc:
                raise TerminationError(
                    f"Failed to delete VMSS '{vmss_name}': {exc}",
                    resource_ids=[vmss_name],
                ) from exc
        elif machine_ids:
            current_capacity = self._get_vmss_capacity(resource_group, vmss_name)
            new_capacity = max(0, current_capacity - len(machine_ids))

            # Native-first cleanup for Flexible VMSS: if this return removes the last
            # member, delete the scale set
            if (
                orchestration_mode == AzureVMSSOrchestrationMode.FLEXIBLE
                and new_capacity == 0
            ):
                self._logger.info(
                    "Deleting flexible VMSS '%s' directly because return would leave it empty",
                    vmss_name,
                )
                try:
                    compute.virtual_machine_scale_sets.begin_delete(
                        resource_group_name=resource_group,
                        vm_scale_set_name=vmss_name,
                    )
                    self._logger.info("Submitted delete for VMSS '%s'", vmss_name)
                    return {
                        "provider_data": {
                            "resource_group": resource_group,
                            "vmss_name": vmss_name,
                            "operation_status": "submitted",
                            "delete_vmss": True,
                        }
                    }
                except Exception as exc:
                    raise TerminationError(
                        f"Failed to delete VMSS '{vmss_name}': {exc}",
                        resource_ids=[vmss_name],
                    ) from exc

            if (
                orchestration_mode != AzureVMSSOrchestrationMode.FLEXIBLE
                and new_capacity != current_capacity
            ):
                self._scale_vmss_capacity(resource_group, vmss_name, new_capacity)

            # Delete specific instances from the VMSS
            self._logger.info(
                "Deleting %d instance(s) from VMSS '%s'",
                len(machine_ids),
                vmss_name,
                )
            try:
                if orchestration_mode == AzureVMSSOrchestrationMode.FLEXIBLE:
                    submitted_deletions: list[dict[str, Any]] = []
                    for vm_name in machine_ids:
                        compute.virtual_machines.begin_delete(
                            resource_group_name=resource_group,
                            vm_name=str(vm_name),
                        )
                        submitted_deletions.append(
                            {
                                "vm_name": str(vm_name),
                            }
                        )
                    self._logger.info(
                        "Submitted delete for %d flexible VMSS instance(s) from '%s'",
                        len(machine_ids),
                        vmss_name,
                    )
                    return {
                        "provider_data": {
                            "resource_group": resource_group,
                            "vmss_name": vmss_name,
                            "operation_status": "submitted",
                            "submitted_deletions": submitted_deletions,
                            "pending_reconciliation": {
                                "resource_group": resource_group,
                                "vmss_name": vmss_name,
                                "machine_ids": [str(machine_id) for machine_id in machine_ids],
                                "target_capacity": new_capacity,
                                "orchestration_mode": orchestration_mode.value,
                                "delete_vmss_when_empty": new_capacity == 0,
                            },
                        }
                    }
                else:
                    resolved_instance_ids = self._resolve_vmss_instance_ids(
                        resource_group=resource_group,
                        vmss_name=vmss_name,
                        machine_ids=machine_ids,
                    )
                    compute.virtual_machine_scale_sets.begin_delete_instances(
                        resource_group_name=resource_group,
                        vm_scale_set_name=vmss_name,
                        vm_instance_i_ds=_build_vmss_delete_instance_ids(
                            resolved_instance_ids
                        ),
                    )
                    self._logger.info(
                        "Submitted delete for %d instance(s) from VMSS '%s'",
                        len(resolved_instance_ids),
                        vmss_name,
                    )
                    return {
                        "provider_data": {
                            "resource_group": resource_group,
                            "vmss_name": vmss_name,
                            "operation_status": "submitted",
                            "resolved_instance_ids": resolved_instance_ids,
                            "pending_reconciliation": {
                                "resource_group": resource_group,
                                "vmss_name": vmss_name,
                                "machine_ids": [str(machine_id) for machine_id in machine_ids],
                                "target_capacity": new_capacity,
                                "orchestration_mode": orchestration_mode.value,
                                "delete_vmss_when_empty": new_capacity == 0,
                            }
                            if new_capacity == 0
                            else None,
                        }
                    }
            except Exception as exc:
                raise TerminationError(
                    f"Failed to delete instances from VMSS '{vmss_name}': {exc}",
                    resource_ids=machine_ids,
                ) from exc
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_vmss_capacity(self, resource_group: str, vmss_name: str) -> int:
        """Return the current desired VMSS capacity."""
        if self.azure_resource_manager:
            capacity_info = self.azure_resource_manager.get_vmss_capacity(
                resource_group=resource_group,
                vmss_name=vmss_name,
            )
            return int(capacity_info.get("capacity") or 0)

        vmss = self.azure_client.compute_client.virtual_machine_scale_sets.get(
            resource_group_name=resource_group,
            vm_scale_set_name=vmss_name,
        )
        sku = getattr(vmss, "sku", None)
        return int(getattr(sku, "capacity", 0) or 0)

    def _scale_vmss_capacity(self, resource_group: str, vmss_name: str, capacity: int) -> None:
        """Scale a VMSS before deleting instances so replacements are not created."""
        self._logger.info(
            "Reducing VMSS '%s' capacity to %d before instance deletion",
            vmss_name,
            capacity,
        )
        if self.azure_resource_manager:
            self.azure_resource_manager.scale_vmss(
                resource_group=resource_group,
                vmss_name=vmss_name,
                capacity=capacity,
            )
            return

        vmss = self.azure_client.compute_client.virtual_machine_scale_sets.get(
            resource_group_name=resource_group,
            vm_scale_set_name=vmss_name,
        )
        vmss.sku.capacity = capacity
        self.azure_client.compute_client.virtual_machine_scale_sets.begin_create_or_update(
            resource_group_name=resource_group,
            vm_scale_set_name=vmss_name,
            parameters=vmss,
        )
        self._logger.info(
            "Submitted fallback VMSS scale for '%s' to capacity %d",
            vmss_name,
            capacity,
        )

    def _resolve_vmss_instance_ids(
        self,
        resource_group: str,
        vmss_name: str,
        machine_ids: list[str],
    ) -> list[str]:
        """Resolve mixed IDs (vm_id/vm_name/instance_id) to VMSS instance IDs."""
        if not machine_ids:
            return []

        compute = self.azure_client.compute_client
        resolved: list[str] = []

        try:
            vms = compute.virtual_machine_scale_set_vms.list(
                resource_group_name=resource_group,
                virtual_machine_scale_set_name=vmss_name,
            )
            lookup: dict[str, str] = {}
            for vm in vms:
                vmss_instance_id = str(getattr(vm, "instance_id", "") or "")
                if not vmss_instance_id:
                    continue
                lookup[vmss_instance_id] = vmss_instance_id

                vm_id = getattr(vm, "vm_id", None)
                if vm_id:
                    lookup[str(vm_id)] = vmss_instance_id

                vm_name = getattr(vm, "name", None)
                if vm_name:
                    lookup[str(vm_name)] = vmss_instance_id

            for machine_id in machine_ids:
                machine_id_str = str(machine_id)
                resolved.append(lookup.get(machine_id_str, machine_id_str))

            if resolved != [str(mid) for mid in machine_ids]:
                self._logger.debug(
                    "Resolved VMSS machine IDs for '%s': %s -> %s",
                    vmss_name,
                    machine_ids,
                    resolved,
                )
            return resolved
        except Exception as exc:
            self._logger.warning(
                "Failed to resolve VMSS instance IDs for '%s', using provided IDs: %s",
                vmss_name,
                exc,
            )
            return [str(mid) for mid in machine_ids]

    def _list_vmss_instances(
        self,
        resource_group: str,
        vmss_name: str,
        include_instance_view: bool = False,
    ) -> list[dict[str, Any]]:
        """Return a list of normalised instance dicts for a VMSS."""
        compute = self.azure_client.compute_client
        orchestration_mode = self._get_vmss_orchestration_mode(resource_group, vmss_name)

        if orchestration_mode == AzureVMSSOrchestrationMode.FLEXIBLE:
            return self._list_flexible_vmss_instances(
                resource_group=resource_group,
                vmss_name=vmss_name,
                include_instance_view=include_instance_view,
            )

        try:
            expand = "instanceView" if include_instance_view else None
            vms = compute.virtual_machine_scale_set_vms.list(
                resource_group_name=resource_group,
                virtual_machine_scale_set_name=vmss_name,
                expand=expand,
            )
        except Exception as exc:
            raise VMSSNotFoundError(
                f"Could not list VMs in VMSS '{vmss_name}': {exc}",
                vmss_name=vmss_name,
            ) from exc

        instances: list[dict[str, Any]] = []
        for vm in vms:
            instance_data = self._normalise_vm(vm, vmss_name, resource_group)
            instances.append(instance_data)
        return instances

    def get_vmss_resource_errors(
        self,
        resource_group: str,
        vmss_name: str,
    ) -> list[dict[str, Any]]:
        """Return VMSS-level provisioning errors even when no instances are visible yet."""
        compute = self.azure_client.compute_client
        try:
            vmss = compute.virtual_machine_scale_sets.get(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
            )
        except Exception:
            return []

        errors: list[dict[str, Any]] = []
        provisioning_state = str(getattr(vmss, "provisioning_state", "") or "")
        statuses = getattr(vmss, "statuses", None) or []

        errors.extend(
            self._extract_vm_errors(
                statuses,
                instance_id=vmss_name,
                vmss_name=vmss_name,
            )
        )

        if provisioning_state.lower() == "failed" and not errors:
            errors.append({
                "error_code": "ProvisioningStateFailed",
                "error_message": f"VMSS '{vmss_name}' provisioning failed",
                "instance_id": vmss_name,
                "resource_id": vmss_name,
                "status_code": "ProvisioningState/failed",
                "status_level": "Error",
            })

        return errors

    def _get_vmss_orchestration_mode(
        self,
        resource_group: str,
        vmss_name: str,
    ) -> AzureVMSSOrchestrationMode:
        compute = self.azure_client.compute_client
        vmss = compute.virtual_machine_scale_sets.get(
            resource_group_name=resource_group,
            vm_scale_set_name=vmss_name,
        )
        raw_mode = getattr(vmss, "orchestration_mode", None) or "Flexible"
        return AzureVMSSOrchestrationMode(str(raw_mode))

    def _list_flexible_vmss_instances(
        self,
        resource_group: str,
        vmss_name: str,
        include_instance_view: bool = False,
    ) -> list[dict[str, Any]]:
        compute = self.azure_client.compute_client

        try:
            vms = list(compute.virtual_machines.list(resource_group_name=resource_group))
        except Exception as exc:
            raise VMSSNotFoundError(
                f"Could not list flexible VMs for VMSS '{vmss_name}': {exc}",
                vmss_name=vmss_name,
            ) from exc

        instances: list[dict[str, Any]] = []

        for vm in vms:
            if not self._is_flexible_vmss_member(vm, vmss_name):
                continue

            if include_instance_view:
                try:
                    vm = compute.virtual_machines.get(
                        resource_group_name=resource_group,
                        vm_name=getattr(vm, "name"),
                        expand="instanceView",
                    )
                except Exception as exc:
                    self._logger.warning(
                        "Failed to fetch instance view for flexible VMSS VM '%s': %s",
                        getattr(vm, "name", "unknown"),
                        exc,
                    )

            instances.append(self._normalise_vm(vm, vmss_name, resource_group))

        return instances

    @staticmethod
    def _is_flexible_vmss_member(vm: Any, vmss_name: str) -> bool:
        """Best-effort membership check for Flexible VMSS VMs."""
        vmss_ref = getattr(vm, "virtual_machine_scale_set", None)
        vmss_ref_id = getattr(vmss_ref, "id", None) if vmss_ref else None
        vmss_arm_suffix = f"/virtualMachineScaleSets/{vmss_name}"
        if vmss_ref_id and str(vmss_ref_id).endswith(vmss_arm_suffix):
            return True

        # Some Azure list responses do not populate virtual_machine_scale_set.
        # Fall back to the VM naming pattern used for Flexible VMSS members.
        candidate_ids = (
            str(getattr(vm, "name", "") or ""),
            str(getattr(vm, "instance_id", "") or ""),
        )
        prefixes = (f"{vmss_name}_", f"{vmss_name}-")
        return any(
            candidate.startswith(prefix)
            for candidate in candidate_ids
            for prefix in prefixes
            if candidate
        )

    def _normalise_vm(
        self, vm: Any, vmss_name: str, resource_group: str
    ) -> dict[str, Any]:
        """Convert an Azure SDK VirtualMachineScaleSetVM to a normalised dict."""
        instance_id = getattr(vm, "instance_id", None) or getattr(vm, "name", "")
        vm_id = getattr(vm, "vm_id", instance_id)

        # Extract status
        status = "unknown"
        instance_view = getattr(vm, "instance_view", None)
        if instance_view and hasattr(instance_view, "statuses"):
            status = _resolve_power_state(instance_view.statuses)
            fleet_errors = self._extract_vm_errors(
                instance_view.statuses,
                instance_id=str(instance_id),
                vmss_name=vmss_name,
            )
        else:
            fleet_errors = []

        # Network IPs / subnet / VNet
        network_identity = self.azure_client.resolve_network_identity_from_vm(vm)
        private_ip = network_identity["private_ip"]
        public_ip = network_identity["public_ip"]
        subnet_id = network_identity["subnet_id"]
        vnet_id = network_identity["vnet_id"]

        # Hardware profile → instance type
        hw = getattr(vm, "hardware_profile", None)
        instance_type = getattr(hw, "vm_size", None) if hw else None
        # Ensure instance_type is a valid non-empty string
        if not instance_type:
            instance_type = "unknown"

        # Location
        location = getattr(vm, "location", None)

        # Zones
        zones = getattr(vm, "zones", None)
        availability_zone = zones[0] if zones else None

        # Launch time approximation
        launch_time = None
        if instance_view and hasattr(instance_view, "statuses"):
            for s in instance_view.statuses:
                t = getattr(s, "time", None)
                if t is not None:
                    launch_time = str(t)
                    break

        return {
            # Use VMSS instance_id as canonical machine id (Azure delete_instances expects this shape).
            "instance_id": str(instance_id),
            "status": status,
            "private_ip": private_ip,
            "public_ip": public_ip,
            "launch_time": launch_time,
            "instance_type": instance_type,
            "subnet_id": subnet_id,
            "vpc_id": vnet_id,
            "availability_zone": availability_zone,
            "provider_type": "azure",
            "provider_data": {
                "resource_id": vmss_name,
                "vmss_name": vmss_name,
                "resource_group": resource_group,
                "vmss_instance_id": instance_id,
                "vm_id": vm_id,
                "location": location,
                "nic_id": network_identity["nic_id"],
                "nic_name": network_identity["nic_name"],
                "vnet_id": vnet_id,
                "fleet_errors": fleet_errors,
            },
        }

    def _extract_vm_errors(
        self,
        statuses: list[Any],
        *,
        instance_id: str,
        vmss_name: str,
    ) -> list[dict[str, Any]]:
        """Extract provisioning failures from Azure instance view statuses."""
        errors: list[dict[str, Any]] = []

        for status in statuses:
            code = str(_status_attr(status, "code", "") or "")
            level = str(_status_attr(status, "level", "") or "")
            message = _status_attr(status, "message")
            display_status = _status_attr(status, "display_status")

            is_failure = code.lower().startswith("provisioningstate/failed") or level.lower() == "error"
            if not is_failure:
                continue

            errors.append({
                "error_code": "ProvisioningStateFailed",
                "error_message": str(message or display_status or code or "Azure provisioning failed"),
                "instance_id": instance_id,
                "resource_id": vmss_name,
                "status_code": code,
                "status_level": level or None,
            })

        return errors

    # ------------------------------------------------------------------
    # Example templates
    # ------------------------------------------------------------------

    @classmethod
    def get_example_templates(cls) -> list[dict[str, Any]]:
        return [
            {
                "template_id": "azure-vmss-linux-basic",
                "name": "Azure VMSS Linux Basic",
                "description": "VMSS with Ubuntu 22.04 LTS on Standard_D4s_v5",
                "provider_type": "azure",
                "provider_api": AzureProviderApi.VMSS.value,
                "vm_size": "Standard_D4s_v5",
                "resource_group": "my-resource-group",
                "location": "eastus2",
                "image": {
                    "publisher": "Canonical",
                    "offer": "0001-com-ubuntu-server-jammy",
                    "sku": "22_04-lts-gen2",
                    "version": "latest",
                },
                "max_instances": 2,
            },
            {
                "template_id": "azure-vmss-spot",
                "name": "Azure VMSS Spot Instances",
                "description": "VMSS with Spot VMs for cost-effective workloads",
                "provider_type": "azure",
                "provider_api": AzureProviderApi.VMSS.value,
                "vm_size": "Standard_D4s_v5",
                "resource_group": "my-resource-group",
                "location": "eastus2",
                "priority": "Spot",
                "eviction_policy": "Deallocate",
                "billing_profile_max_price": -1.0,
                "image": {
                    "publisher": "Canonical",
                    "offer": "0001-com-ubuntu-server-jammy",
                    "sku": "22_04-lts-gen2",
                    "version": "latest",
                },
                "max_instances": 5,
            },
        ]
