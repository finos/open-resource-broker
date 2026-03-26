"""SingleVM Handler - provisions individual VMs via the Azure Compute SDK.

This handler is used when ``provider_api == "SingleVM"`` in the template.
It creates standalone Virtual Machines rather than VMSS instances, which
is suitable for long-lived singleton workloads.
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
    LaunchError,
    TerminationError,
)
from orb.providers.azure.infrastructure.error_utils import (
    canonical_azure_error_code,
    extract_azure_error_details,
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
        resource_group = template.resource_group
        location = template.location
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

        # Resolve ssh_key_name → actual key data if needed.
        # Done once before the loop so
        # that _build_vm_params can read the cached ssh_public_keys.
        if template.ssh_key_name and not template.ssh_public_keys:
            template.resolve_ssh_keys(self.azure_client.compute_client)

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
                    vm_params = self._build_vm_params(
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
                network_identity = self.azure_client.resolve_network_identity_from_vm(vm)
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
                poller = compute.virtual_machines.begin_delete(
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

            resolved = [lookup.get(str(machine_id), str(machine_id)) for machine_id in machine_ids]
            if resolved != [str(mid) for mid in machine_ids]:
                self._logger.debug(
                    "Resolved SingleVM IDs in resource_group '%s': %s -> %s",
                    resource_group,
                    machine_ids,
                    resolved,
                )
            return resolved
        except Exception as exc:
            self._logger.warning(
                "Failed to resolve VM names, using provided IDs directly: %s",
                exc,
            )
            return [str(machine_id) for machine_id in machine_ids]

    def _resolve_subnet_id(self, template: AzureTemplate) -> Optional[str]:
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

    @staticmethod
    def _build_vm_params(
            template: AzureTemplate, vm_name: str, nic_id: str, vm_size_override: Optional[str] = None
    ) -> dict[str, Any]:
        """Build ARM VM create parameters for a single VM deployment."""
        params: dict[str, Any] = {
            "location": template.location,
            "properties": {
                "hardwareProfile": {"vmSize": vm_size_override or template.vm_size},
                "storageProfile": {},
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

        # Image
        if template.image:
            params["properties"]["storageProfile"]["imageReference"] = (
                template.image.to_arm_dict()
            )
        elif template.image_id:
            params["properties"]["storageProfile"]["imageReference"] = {
                "id": template.image_id
            }

        # OS disk
        if template.os_disk:
            params["properties"]["storageProfile"]["osDisk"] = (
                template.os_disk.to_arm_dict()
            )
        else:
            params["properties"]["storageProfile"]["osDisk"] = {
                "createOption": "FromImage",
                "deleteOption": "Delete",
                "managedDisk": {"storageAccountType": "Standard_LRS"},
            }

        # Data disks
        if template.data_disks:
            params["properties"]["storageProfile"]["dataDisks"] = [
                disk.to_arm_dict() for disk in template.data_disks
            ]

        if template.disk_encryption_set_id:
            os_disk_managed = params["properties"]["storageProfile"].get("osDisk", {}).get("managedDisk")
            if isinstance(os_disk_managed, dict):
                os_disk_managed["diskEncryptionSet"] = {
                    "id": template.disk_encryption_set_id,
                }

            for data_disk in params["properties"]["storageProfile"].get("dataDisks", []):
                managed_disk = data_disk.get("managedDisk")
                if isinstance(managed_disk, dict):
                    managed_disk["diskEncryptionSet"] = {
                        "id": template.disk_encryption_set_id,
                    }

        # SSH keys
        if template.ssh_public_keys:
            params["properties"]["osProfile"]["linuxConfiguration"] = {
                "disablePasswordAuthentication": True,
                "ssh": {
                    "publicKeys": [
                        {
                            "path": f"/home/{template.admin_username}/.ssh/authorized_keys",
                            "keyData": key,
                        }
                        for key in template.ssh_public_keys
                    ]
                },
            }

        # Custom data
        if template.custom_data:
            params["properties"]["osProfile"]["customData"] = template.custom_data

        # Priority / Spot
        if template.priority.value != "Regular":
            params["properties"]["priority"] = template.priority.value
            if template.eviction_policy:
                params["properties"]["evictionPolicy"] = template.eviction_policy.value
            if template.billing_profile_max_price is not None:
                params["properties"]["billingProfile"] = {
                    "maxPrice": template.billing_profile_max_price
                }

        # Security profile
        if template.security_type:
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
            params["properties"]["securityProfile"] = security_profile

        # Capacity reservation
        if template.capacity_reservation_group_id:
            params["properties"]["capacityReservation"] = {
                "capacityReservationGroup": {
                    "id": template.capacity_reservation_group_id,
                }
            }

        # Identity
        if template.system_assigned_identity or template.user_assigned_identity_ids:
            identity: dict[str, Any]
            if template.system_assigned_identity and template.user_assigned_identity_ids:
                identity = {
                    "type": "SystemAssigned, UserAssigned",
                    "userAssignedIdentities": {
                        uid: {} for uid in template.user_assigned_identity_ids
                    },
                }
            elif template.system_assigned_identity:
                identity = {"type": "SystemAssigned"}
            else:
                identity = {
                    "type": "UserAssigned",
                    "userAssignedIdentities": {
                        uid: {} for uid in template.user_assigned_identity_ids
                    },
                }
            params["identity"] = identity

        # Availability zones
        if template.zones:
            params["zones"] = template.zones

        return params

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
