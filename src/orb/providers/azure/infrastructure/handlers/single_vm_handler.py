"""SingleVM Handler - provisions individual VMs via the Azure Compute SDK.

This handler is used when ``provider_api == "SingleVM"`` in the template.
It creates standalone Virtual Machines rather than VMSS instances, which
is suitable for long-lived singleton workloads.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from azure.core.exceptions import ResourceNotFoundError as AzureResourceNotFoundError
from orb.domain.base.dependency_injection import injectable
from orb.domain.request.aggregate import Request
from orb.infrastructure.di.container import get_container
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions.azure_exceptions import (
    LaunchError,
    TerminationError,
)
from orb.providers.azure.infrastructure.error_utils import (
    canonical_azure_error_code,
    extract_azure_error_details,
)
from orb.providers.azure.infrastructure.handlers._network_identity import (
    empty_network_identity,
    network_identity_soft_failure_types,
)
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureHandler


_AZURE_STATE_MAP: dict[str, str] = {
    "PowerState/starting": "pending",
    "PowerState/running": "running",
    "PowerState/stopping": "stopping",
    "PowerState/stopped": "stopped",
    "PowerState/deallocating": "shutting-down",
    "PowerState/deallocated": "stopped",
}

def _resolve_power_state(statuses: list[Any]) -> str:
    for status in statuses:
        code = status.code if hasattr(status, "code") else str(status.get("code", ""))
        if code.startswith("PowerState/"):
            return _AZURE_STATE_MAP.get(code, "unknown")
    return "unknown"


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


@injectable
class SingleVMHandler(AzureHandler):
    """Handler that creates and manages individual Azure VMs.

    ``provider_api = "SingleVM"``
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        from orb.providers.azure.infrastructure.services.azure_deployment_service import (
            AzureDeploymentService,
        )

        self.azure_deployment_service = AzureDeploymentService(
            azure_client=self.azure_client,
            logger=self._logger,
        )
        container = get_container()
        try:
            from orb.providers.azure.infrastructure.services.azure_native_spec_service import (
                AzureNativeSpecService,
            )

            self.azure_native_spec_service = container.get(AzureNativeSpecService)
        except Exception:
            self.azure_native_spec_service = None

    def acquire_hosts(
        self, request: Request, template: AzureTemplate
    ) -> dict[str, Any]:
        """Create one or more individual VMs.

        VM create operations are submitted and tracked via later status checks,
        rather than blocking here until each LRO completes.
        """
        resource_group = template.resource_group.value
        location = template.location.value
        count = request.requested_count

        # Resolve subnet for NIC creation
        subnet_id = self._resolve_subnet_id(template)
        if not subnet_id:
            raise LaunchError(
                message=(
                    "No subnet specified. Add 'subnet_id' (full ARM resource ID) "
                    "to the template under subnet_ids or network_config, e.g.: "
                    "/subscriptions/<sub>/resourceGroups/<rg>/providers/"
                    "Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>"
                ),
                template_id=template.template_id,
            )

        self._logger.info(
            "Creating %d individual VM(s) in resource group '%s' (location=%s)",
            count,
            resource_group,
            location,
        )

        nsg_id = (
            template.network_config.network_security_group_id
            if template.network_config else None
        )
        accel_net = bool(
            template.network_config.accelerated_networking
            if template.network_config else False
        )
        backend_pool_ids = (
            template.network_config.load_balancer_backend_pool_ids
            if template.network_config else []
        )
        inbound_nat_pool_ids = (
            template.network_config.load_balancer_inbound_nat_pool_ids
            if template.network_config else []
        )
        app_gateway_pool_ids = (
            template.network_config.application_gateway_backend_pool_ids
            if template.network_config else []
        )
        public_ip_enabled = bool(
            template.network_config.public_ip_enabled
            if template.network_config else False
        )

        # Resolve ssh_key_name → actual key data once before the loop.
        # Build a local template copy with resolved keys so the mapper
        # can read them without mutating the original aggregate.
        resolved_ssh_keys = list(template.ssh_public_keys)
        if template.ssh_key_name and not resolved_ssh_keys:
            from orb.providers.azure.infrastructure.services.ssh_key_resolver import (
                resolve_ssh_keys,
            )

            resolved_ssh_keys = resolve_ssh_keys(
                ssh_key_name=template.ssh_key_name,
                ssh_public_keys=template.ssh_public_keys,
                resource_group=template.resource_group.value,
                compute_client=self.azure_client.compute_client,
            )
        if resolved_ssh_keys != list(template.ssh_public_keys):
            template = template.model_copy(update={"ssh_public_keys": resolved_ssh_keys})

        candidate_vm_sizes = [template.vm_size, *(template.vm_sizes or [])]
        vm_definitions: list[dict[str, Any]] = []
        for _ in range(count):
            vm_name = f"vm-{template.template_id}-{uuid.uuid4().hex[:8]}"
            vm_definitions.append({
                "vm_name": vm_name,
                "nic_name": f"nic-{vm_name}",
                "public_ip_name": f"pip-{vm_name}" if public_ip_enabled else None,
            })

        selected_vm_size: Optional[str] = None
        submitted_deployment_name: Optional[str] = None
        last_error_details: Optional[dict[str, Any]] = None

        for candidate_vm_size in candidate_vm_sizes:
            try:
                resolved_vm_definitions: list[dict[str, Any]] = []
                for vm_definition in vm_definitions:
                    nic_id = self.azure_deployment_service.resource_id_expression(
                        "Microsoft.Network/networkInterfaces",
                        vm_definition["nic_name"],
                    )
                    from orb.providers.azure.infrastructure.services.arm_payload_mapper import (
                        ArmPayloadMapper,
                    )

                    vm_params = ArmPayloadMapper.single_vm_payload(
                        template,
                        vm_definition["vm_name"],
                        nic_id,
                        vm_size_override=candidate_vm_size,
                    )
                    if self.azure_native_spec_service:
                        merged_params = (
                            self.azure_native_spec_service.process_provider_api_spec_with_merge(
                                template=template,
                                request=request,
                                default_payload=vm_params,
                                extra_context={
                                    "vm_name": vm_definition["vm_name"],
                                    "nic_id": nic_id,
                                    "vm_size": candidate_vm_size,
                                },
                            )
                        )
                        if merged_params:
                            vm_params = merged_params

                    resolved_vm_definitions.append({
                        **vm_definition,
                        "vm_payload": vm_params,
                    })

                deployment_name = self.azure_deployment_service.build_deployment_name(
                    "vm",
                    str(request.request_id),
                    template.template_id,
                    candidate_vm_size,
                )
                deployment_template = (
                    self.azure_deployment_service.build_single_vm_deployment_template(
                        location=location,
                        subnet_id=subnet_id,
                        vm_definitions=resolved_vm_definitions,
                        enable_accelerated_networking=accel_net,
                        nsg_id=nsg_id,
                        load_balancer_backend_pool_ids=backend_pool_ids,
                        load_balancer_inbound_nat_pool_ids=inbound_nat_pool_ids,
                        application_gateway_backend_pool_ids=app_gateway_pool_ids,
                    )
                )
                submitted_deployment_name = self.azure_deployment_service.submit_template_deployment(
                    resource_group=resource_group,
                    deployment_name=deployment_name,
                    template=deployment_template,
                )
                selected_vm_size = candidate_vm_size
                break
            except Exception as exc:
                error_details = extract_azure_error_details(exc)
                last_error_details = {
                    "error_code": self._classify_provisioning_error(exc),
                    "error_message": error_details["message"],
                    "resource_group": resource_group,
                    "instance_type": candidate_vm_size,
                    "status_code": error_details["status_code"],
                    "raw_error_code": error_details["raw_error_code"],
                }
                if not self._is_capacity_error(exc):
                    break

        if submitted_deployment_name is None or selected_vm_size is None:
            raise LaunchError(
                message=(
                    last_error_details["error_message"]
                    if last_error_details
                    else "Failed to submit SingleVM deployment"
                ),
                template_id=template.template_id,
                error_code=(
                    last_error_details["error_code"] if last_error_details else None
                ),
            )

        created_ids = [vm_definition["vm_name"] for vm_definition in vm_definitions]
        operation_tracking = [
            {
                "vm_name": vm_definition["vm_name"],
                "nic_name": vm_definition["nic_name"],
                "public_ip_name": vm_definition["public_ip_name"],
                "selected_vm_size": selected_vm_size,
            }
            for vm_definition in vm_definitions
        ]
        self._logger.info(
            "Submitted create deployment '%s' for %d VM(s)",
            submitted_deployment_name,
            len(created_ids),
        )

        return {
            "success": True,
            "resource_ids": created_ids,
            "instances": [],
            "error_message": None,
            "provider_data": {
                "resource_group": resource_group,
                "location": location,
                "submitted_count": len(created_ids),
                "operation_status": "submitted",
                # Azure async create returns resource tracking first and instances later.
                # Mark the submit attempt as final so generic top-up retry logic does not
                # resubmit the deployment and create duplicate VMs.
                "fulfillment_final": True,
                "deployment_name": submitted_deployment_name,
                "error_codes": [],
                "fleet_errors": [],
                "submitted_vms": operation_tracking,
            },
        }

    def check_hosts_status(self, request: Request) -> list[dict[str, Any]]:
        """Return status for individual VM IDs."""
        resource_ids: list[str] = getattr(request, "resource_ids", []) or []
        resource_group = (
            (request.metadata or {}).get("resource_group")
            or self.azure_client.resource_group
        )
        if not resource_group:
            self._logger.error("Cannot resolve resource_group for status check")
            return []

        results: list[dict[str, Any]] = []
        compute = self.azure_client.compute_client
        resolved_vm_names = self._resolve_vm_names(resource_group, resource_ids)

        for original_id, vm_name in zip(resource_ids, resolved_vm_names):
            try:
                vm = compute.virtual_machines.get(
                    resource_group_name=resource_group,
                    vm_name=vm_name,
                    expand="instanceView",
                )
                status = "unknown"
                instance_view = getattr(vm, "instance_view", None)
                if instance_view and hasattr(instance_view, "statuses"):
                    status = _resolve_power_state(instance_view.statuses)

                hw = getattr(vm, "hardware_profile", None)
                network_identity = empty_network_identity()
                try:
                    network_identity = self.azure_client.resolve_network_identity_from_vm(vm)
                except network_identity_soft_failure_types() as exc:
                    # Optional NIC/IP enrichment must not hide an otherwise visible VM.
                    self._logger.warning(
                        "Failed to resolve network identity for VM '%s': %s",
                        vm_name,
                        exc,
                    )
                results.append({
                    "instance_id": getattr(vm, "name", original_id),
                    "status": status,
                    "private_ip": network_identity["private_ip"],
                    "public_ip": network_identity["public_ip"],
                    "launch_time": None,
                    "instance_type": getattr(hw, "vm_size", None) if hw else None,
                    "subnet_id": network_identity["subnet_id"],
                    "vpc_id": network_identity["vnet_id"],
                    "availability_zone": (getattr(vm, "zones", None) or [None])[0],
                    "provider_type": "azure",
                    "provider_data": {
                        "resource_id": getattr(vm, "name", vm_name),
                        "vm_name": getattr(vm, "name", vm_name),
                        "vm_id": getattr(vm, "vm_id", None),
                        "resource_group": resource_group,
                        "location": getattr(vm, "location", None),
                        "nic_id": network_identity["nic_id"],
                        "nic_name": network_identity["nic_name"],
                        "vnet_id": network_identity["vnet_id"],
                    },
                })
            except Exception as exc:
                self._logger.error("Failed to get status for VM '%s': %s", vm_name, exc)

        return results

    def release_hosts(
        self,
        machine_ids: list[str],
        resource_id: str,
        context: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Submit deletion for individual VMs.

        Azure-native delete options are set on attached resources during
        provisioning, so termination can remain submit-and-return without ORB
        performing dependent-resource cleanup.
        """
        context = context or {}
        resource_group = (
            context.get("resource_group") or self.azure_client.resource_group
        )
        if not resource_group:
            raise TerminationError(
                "resource_group is required for release_hosts",
                resource_ids=machine_ids,
            )

        compute = self.azure_client.compute_client
        vm_names = self._resolve_vm_names(resource_group, machine_ids)
        submitted_deletions: list[dict[str, Any]] = []
        for original_id, vm_name in zip(machine_ids, vm_names):
            try:
                self._logger.info(
                    "Deleting VM '%s' (requested id='%s')", vm_name, original_id
                )
                compute.virtual_machines.begin_delete(
                    resource_group_name=resource_group,
                    vm_name=vm_name,
                )
                submitted_deletions.append(
                    {
                        "requested_id": str(original_id),
                        "vm_name": vm_name,
                    }
                )
            except Exception as exc:
                self._logger.error("Failed to delete VM '%s': %s", vm_name, exc)
                raise TerminationError(
                    f"Failed to delete VM '{vm_name}': {exc}",
                    resource_ids=[original_id],
                ) from exc

        return {
            "provider_data": {
                "resource_group": resource_group,
                "operation_status": "submitted",
                "submitted_deletions": submitted_deletions,
            }
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_vm_names(self, resource_group: str, machine_ids: list[str]) -> list[str]:
        """Resolve mixed IDs (vm_name or Azure vm_id GUID) to VM names."""
        if not machine_ids:
            return []

        try:
            compute = self.azure_client.compute_client
            resolved: list[Optional[str]] = [None] * len(machine_ids)
            unresolved_indices: list[int] = []

            for index, machine_id in enumerate(machine_ids):
                machine_id_str = str(machine_id)
                if _looks_like_uuid(machine_id_str):
                    unresolved_indices.append(index)
                    continue

                try:
                    vm = compute.virtual_machines.get(
                        resource_group_name=resource_group,
                        vm_name=machine_id_str,
                    )
                    resolved[index] = str(getattr(vm, "name", None) or machine_id_str)
                except AzureResourceNotFoundError:
                    unresolved_indices.append(index)

            if unresolved_indices:
                vms = compute.virtual_machines.list(resource_group_name=resource_group)

                lookup: dict[str, str] = {}
                for vm in vms:
                    vm_name = getattr(vm, "name", None)
                    if not vm_name:
                        continue
                    lookup[str(vm_name)] = str(vm_name)

                    vm_id = getattr(vm, "vm_id", None)
                    if vm_id:
                        lookup[str(vm_id)] = str(vm_name)

                for index in unresolved_indices:
                    machine_id = str(machine_ids[index])
                    resolved[index] = lookup.get(machine_id, machine_id)

            ordered_resolved = [
                resolved_name if resolved_name is not None else str(machine_id)
                for machine_id, resolved_name in zip(machine_ids, resolved)
            ]

            if ordered_resolved != [str(mid) for mid in machine_ids]:
                self._logger.debug(
                    "Resolved SingleVM IDs in resource_group '%s': %s -> %s",
                    resource_group,
                    machine_ids,
                    ordered_resolved,
                )
            return ordered_resolved
        except Exception as exc:
            self._logger.warning(
                "Failed to resolve VM names, using provided IDs directly: %s",
                exc,
            )
            return [str(machine_id) for machine_id in machine_ids]

    @staticmethod
    def _resolve_subnet_id(template: AzureTemplate) -> Optional[str]:
        """Return the subnet ARM ID from network_config or subnet_ids."""
        if template.network_config and template.network_config.subnet_id:
            return template.network_config.subnet_id
        # subnet_ids is the base Template field; subnet_id is a @property returning [0]
        if template.subnet_ids:
            candidate = template.subnet_ids[0]
            if candidate and candidate != "default-subnet":
                return candidate
        return None

    @staticmethod
    def _classify_provisioning_error(exc: Exception) -> str:
        """Map common Azure provisioning failures to stable error codes."""
        return canonical_azure_error_code(exc)

    @staticmethod
    def _is_capacity_error(exc: Exception) -> bool:
        """Return True when trying another candidate VM size is reasonable."""
        return canonical_azure_error_code(exc) in {
            "AllocationFailed",
            "ZonalAllocationFailed",
            "SkuNotAvailable",
            "OverconstrainedAllocationRequest",
        }

    # NIC and Public IP cleanup is handled natively by Azure via
    # deleteOption: "Delete" on the NIC and Public IP references in the
    # ARM template.  When a VM is deleted, Azure cascades the deletion
    # through NIC → Public IP automatically.  For Flexible VMSS this is
    # the default behaviour.  No ORB-managed rollback methods are needed.
    # See: https://learn.microsoft.com/en-us/azure/virtual-machines/delete

    @classmethod
    def get_example_templates(cls) -> list[dict[str, Any]]:
        return [
            {
                "template_id": "azure-singlevm-linux",
                "name": "Azure Single VM Linux",
                "description": "Individual Ubuntu 22.04 VM on Standard_D2s_v5",
                "provider_type": "azure",
                "provider_api": AzureProviderApi.SINGLE_VM.value,
                "vm_size": "Standard_D2s_v5",
                "resource_group": "my-resource-group",
                "location": "eastus2",
                "image": {
                    "publisher": "Canonical",
                    "offer": "0001-com-ubuntu-server-jammy",
                    "sku": "22_04-lts-gen2",
                    "version": "latest",
                },
                "max_instances": 5,
            },
        ]
