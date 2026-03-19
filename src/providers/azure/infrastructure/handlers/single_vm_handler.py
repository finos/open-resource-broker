"""SingleVM Handler - provisions individual VMs via the Azure Compute SDK.

This handler is used when ``provider_api == "SingleVM"`` in the template.
It creates standalone Virtual Machines rather than VMSS instances, which
is suitable for long-lived singleton workloads.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from domain.base.dependency_injection import injectable
from domain.request.aggregate import Request
from infrastructure.di.container import get_container
from providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from providers.azure.domain.template.value_objects import AzureProviderApi
from providers.azure.exceptions.azure_exceptions import (
    LaunchError,
    TerminationError,
)
from providers.azure.infrastructure.error_utils import (
    canonical_azure_error_code,
    extract_azure_error_details,
)
from providers.azure.infrastructure.handlers.azure_handler import AzureHandler


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
        container = get_container()
        try:
            from providers.azure.infrastructure.services.azure_native_spec_service import (
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

        created_ids: list[str] = []
        errors: list[str] = []
        structured_errors: list[dict[str, Any]] = []
        created_nic_names: list[str] = []
        operation_tracking: list[dict[str, Any]] = []

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

        # Resolve ssh_key_name → actual key data if needed.
        # Done once before the loop so
        # that _build_vm_params can read the cached ssh_public_keys.
        if template.ssh_key_name and not template.ssh_public_keys:
            template.resolve_ssh_keys(self.azure_client.compute_client)

        candidate_vm_sizes = [template.vm_size, *(template.vm_sizes or [])]

        for i in range(count):
            vm_name = f"vm-{template.template_id}-{uuid.uuid4().hex[:8]}"
            nic_name = f"nic-{vm_name}"
            nic_id: Optional[str] = None
            try:
                # Step 1: create NIC
                nic_id = self._create_nic(
                    vm_name=vm_name,
                    resource_group=resource_group,
                    location=location,
                    subnet_id=subnet_id,
                    enable_accelerated_networking=accel_net,
                    nsg_id=nsg_id,
                    load_balancer_backend_pool_ids=backend_pool_ids,
                    load_balancer_inbound_nat_pool_ids=inbound_nat_pool_ids,
                    application_gateway_backend_pool_ids=app_gateway_pool_ids,
                )
                created_nic_names.append(nic_name)
                self._logger.debug("NIC '%s' created: %s", nic_name, nic_id)

                # Step 2: create VM
                compute = self.azure_client.compute_client
                poller = None
                selected_vm_size: Optional[str] = None
                vm_attempt_errors: list[dict[str, Any]] = []

                for candidate_vm_size in candidate_vm_sizes:
                    try:
                        vm_params = self._build_vm_params(
                            template,
                            vm_name,
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
                                        "vm_name": vm_name,
                                        "nic_id": nic_id,
                                        "vm_size": candidate_vm_size,
                                    },
                                )
                            )
                            if merged_params:
                                vm_params = merged_params
                        poller = compute.virtual_machines.begin_create_or_update(
                            resource_group_name=resource_group,
                            vm_name=vm_name,
                            parameters=vm_params,
                        )
                        selected_vm_size = candidate_vm_size
                        break
                    except Exception as exc:
                        error_details = extract_azure_error_details(exc)
                        vm_attempt_errors.append({
                            "error_code": self._classify_provisioning_error(exc),
                            "error_message": error_details["message"],
                            "instance_id": vm_name,
                            "resource_group": resource_group,
                            "instance_type": candidate_vm_size,
                            "status_code": error_details["status_code"],
                            "raw_error_code": error_details["raw_error_code"],
                        })
                        if not self._is_capacity_error(exc):
                            break

                if poller is None or selected_vm_size is None:
                    last_error = vm_attempt_errors[-1] if vm_attempt_errors else None
                    raise LaunchError(
                        message=(
                            last_error["error_message"]
                            if last_error
                            else f"Failed to create VM '{vm_name}'"
                        ),
                        template_id=template.template_id,
                    )
                # Use VM name as ORB machine ID; it is directly actionable for Azure APIs.
                created_ids.append(vm_name)
                continuation_token = None
                if hasattr(poller, "continuation_token"):
                    try:
                        continuation_token = poller.continuation_token()
                    except Exception as exc:
                        self._logger.debug(
                            "Could not capture VM poller continuation token for '%s': %s",
                            vm_name,
                            exc,
                        )

                operation_tracking.append({
                    "vm_name": vm_name,
                    "nic_name": nic_name,
                    "selected_vm_size": selected_vm_size,
                    "continuation_token": continuation_token,
                })
                self._logger.info("Submitted create operation for VM '%s'", vm_name)

            except Exception as exc:
                error_msg = f"Failed to create VM '{vm_name}': {exc}"
                self._logger.error(error_msg)
                errors.append(error_msg)
                if "vm_attempt_errors" in locals() and vm_attempt_errors:
                    structured_errors.extend(vm_attempt_errors)
                else:
                    error_details = extract_azure_error_details(exc)
                    structured_errors.append({
                        "error_code": self._classify_provisioning_error(exc),
                        "error_message": error_details["message"],
                        "instance_id": vm_name,
                        "resource_group": resource_group,
                        "instance_type": template.vm_size,
                        "status_code": error_details["status_code"],
                        "raw_error_code": error_details["raw_error_code"],
                    })
                # Clean up the NIC we just created (best-effort)
                if nic_id:
                    self._delete_nic(resource_group, nic_name)
                    created_nic_names = [n for n in created_nic_names if n != nic_name]

        if not created_ids:
            raise LaunchError(
                message=f"Failed to create any VMs: {'; '.join(errors)}",
                template_id=template.template_id,
            )

        return {
            "success": True,
            "resource_ids": created_ids,
            "instances": [],
            "error_message": "; ".join(errors) if errors else None,
            "provider_data": {
                "resource_group": resource_group,
                "location": location,
                "created_count": len(created_ids),
                "failed_count": len(errors),
                "operation_status": "partial_submitted" if structured_errors else "submitted",
                "fleet_errors": structured_errors,
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

        Important limitation:
        - Azure VM deletion is now submit-and-return to align with the async
          behavior used elsewhere in the provider and with AWS termination.
        - Linked NIC/public IP/disk cleanup is therefore no longer performed
          inline here, because those resources may still be attached while the
          VM delete LRO is in progress.
        - Cleanup remains a follow-up concern for later reconciliation work.
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
                continuation_token = None
                if hasattr(poller, "continuation_token"):
                    try:
                        continuation_token = poller.continuation_token()
                    except Exception as exc:
                        self._logger.debug(
                            "Could not capture VM delete continuation token for '%s': %s",
                            vm_name,
                            exc,
                        )
                submitted_deletions.append(
                    {
                        "requested_id": str(original_id),
                        "vm_name": vm_name,
                        "continuation_token": continuation_token,
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
                "cleanup_deferred": True,
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

    def _create_nic(
        self,
        vm_name: str,
        resource_group: str,
        location: str,
        subnet_id: str,
        enable_accelerated_networking: bool = False,
        nsg_id: Optional[str] = None,
        load_balancer_backend_pool_ids: Optional[list[str]] = None,
        load_balancer_inbound_nat_pool_ids: Optional[list[str]] = None,
        application_gateway_backend_pool_ids: Optional[list[str]] = None,
    ) -> str:
        """Create a NIC and return its ARM resource ID."""
        nic_name = f"nic-{vm_name}"
        ip_config_properties: dict[str, Any] = {
            "subnet": {"id": subnet_id},
            "privateIPAllocationMethod": "Dynamic",
        }
        if load_balancer_backend_pool_ids:
            ip_config_properties["loadBalancerBackendAddressPools"] = [
                {"id": pool_id} for pool_id in load_balancer_backend_pool_ids
            ]
        if load_balancer_inbound_nat_pool_ids:
            ip_config_properties["loadBalancerInboundNatPools"] = [
                {"id": pool_id} for pool_id in load_balancer_inbound_nat_pool_ids
            ]
        if application_gateway_backend_pool_ids:
            ip_config_properties["applicationGatewayBackendAddressPools"] = [
                {"id": pool_id} for pool_id in application_gateway_backend_pool_ids
            ]

        nic_params: dict[str, Any] = {
            "location": location,
            "properties": {
                "ipConfigurations": [
                    {
                        "name": "ipconfig1",
                        "properties": ip_config_properties,
                    }
                ],
                "enableAcceleratedNetworking": enable_accelerated_networking,
            },
        }
        if nsg_id:
            nic_params["properties"]["networkSecurityGroup"] = {"id": nsg_id}

        network = self.azure_client.network_client
        poller = network.network_interfaces.begin_create_or_update(
            resource_group_name=resource_group,
            network_interface_name=nic_name,
            parameters=nic_params,
        )
        nic_result = poller.result()
        return nic_result.id

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
                        {"id": nic_id, "properties": {"primary": True}}
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
                "managedDisk": {"storageAccountType": "Standard_LRS"},
            }

        # Data disks
        if template.data_disks:
            params["properties"]["storageProfile"]["dataDisks"] = [
                disk.to_arm_dict() for disk in template.data_disks
            ]

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

    def _delete_nic(self, resource_group: str, nic_name: str) -> None:
        """Delete a NIC and any linked public IPs if they are not attached."""
        try:
            network = self.azure_client.network_client
            nic = network.network_interfaces.get(
                resource_group_name=resource_group,
                network_interface_name=nic_name,
            )

            attached_vm = getattr(nic, "virtual_machine", None)
            if attached_vm and getattr(attached_vm, "id", None):
                self._logger.info(
                    "Skipping NIC '%s' deletion because it is still attached to VM '%s'",
                    nic_name,
                    getattr(attached_vm, "id", "unknown"),
                )
                return

            public_ip_targets: list[tuple[str, str]] = []
            for ip_cfg in getattr(nic, "ip_configurations", []) or []:
                ip_props = getattr(ip_cfg, "public_ip_address", None)
                pip_id = getattr(ip_props, "id", None)
                if pip_id:
                    rg_name = self._extract_resource_group_and_name_from_arm_id(str(pip_id))
                    if rg_name:
                        public_ip_targets.append(rg_name)

            network.network_interfaces.begin_delete(
                resource_group_name=resource_group,
                network_interface_name=nic_name,
            ).result()

            for pip_rg, pip_name in public_ip_targets:
                self._delete_public_ip_if_unattached(pip_rg, pip_name)
        except Exception as exc:
            self._logger.warning("Could not delete NIC '%s': %s", nic_name, exc)

    def _collect_vm_cleanup_targets(
        self,
        resource_group: str,
        vm_name: str,
    ) -> dict[str, list[tuple[str, str]]]:
        """Collect linked NIC and managed-disk names for post-VM deletion cleanup."""
        cleanup: dict[str, list[tuple[str, str]]] = {"nics": [], "disks": []}
        try:
            compute = self.azure_client.compute_client
            vm = compute.virtual_machines.get(
                resource_group_name=resource_group,
                vm_name=vm_name,
            )

            net_profile = getattr(vm, "network_profile", None)
            for nic_ref in getattr(net_profile, "network_interfaces", []) or []:
                nic_id = getattr(nic_ref, "id", None)
                if nic_id:
                    rg_name = self._extract_resource_group_and_name_from_arm_id(str(nic_id))
                    if rg_name:
                        cleanup["nics"].append(rg_name)

            storage_profile = getattr(vm, "storage_profile", None)
            os_disk = getattr(storage_profile, "os_disk", None)
            os_managed_disk = getattr(os_disk, "managed_disk", None) if os_disk else None
            os_disk_id = getattr(os_managed_disk, "id", None)
            if os_disk_id:
                rg_name = self._extract_resource_group_and_name_from_arm_id(str(os_disk_id))
                if rg_name:
                    cleanup["disks"].append(rg_name)

            for data_disk in getattr(storage_profile, "data_disks", []) or []:
                managed_disk = getattr(data_disk, "managed_disk", None)
                data_disk_id = getattr(managed_disk, "id", None)
                if data_disk_id:
                    rg_name = self._extract_resource_group_and_name_from_arm_id(
                        str(data_disk_id)
                    )
                    if rg_name:
                        cleanup["disks"].append(rg_name)
        except Exception as exc:
            self._logger.warning(
                "Failed to collect cleanup targets for VM '%s': %s",
                vm_name,
                exc,
            )

        return {
            "nics": list(dict.fromkeys(cleanup["nics"])),
            "disks": list(dict.fromkeys(cleanup["disks"])),
        }

    def _delete_public_ip_if_unattached(self, resource_group: str, ip_name: str) -> None:
        """Delete a public IP only when it is detached from all configurations."""
        try:
            network = self.azure_client.network_client
            public_ip = network.public_ip_addresses.get(
                resource_group_name=resource_group,
                public_ip_address_name=ip_name,
            )
            if getattr(public_ip, "ip_configuration", None):
                self._logger.info(
                    "Skipping public IP '%s' deletion because it is still attached",
                    ip_name,
                )
                return

            network.public_ip_addresses.begin_delete(
                resource_group_name=resource_group,
                public_ip_address_name=ip_name,
            ).result()
        except Exception as exc:
            self._logger.warning("Could not delete public IP '%s': %s", ip_name, exc)

    def _delete_managed_disk_if_unattached(
        self,
        resource_group: str,
        disk_name: str,
    ) -> None:
        """Delete a managed disk if it is not currently attached to a VM."""
        try:
            compute = self.azure_client.compute_client
            disk = compute.disks.get(
                resource_group_name=resource_group,
                disk_name=disk_name,
            )
            if getattr(disk, "managed_by", None):
                self._logger.info(
                    "Skipping disk '%s' deletion because it is still attached to '%s'",
                    disk_name,
                    getattr(disk, "managed_by", "unknown"),
                )
                return

            compute.disks.begin_delete(
                resource_group_name=resource_group,
                disk_name=disk_name,
            ).result()
        except Exception as exc:
            self._logger.warning("Could not delete managed disk '%s': %s", disk_name, exc)

    def _extract_resource_group_and_name_from_arm_id(
        self,
        arm_id: str,
    ) -> Optional[tuple[str, str]]:
        """Extract (resource_group, name) from a standard ARM resource ID."""
        parts = [segment for segment in arm_id.split("/") if segment]
        if not parts:
            return None

        try:
            rg_index = next(
                idx
                for idx, value in enumerate(parts)
                if value.lower() == "resourcegroups"
            )
            resource_group = parts[rg_index + 1]
            resource_name = parts[-1]
            if resource_group and resource_name:
                return resource_group, resource_name
            return None
        except Exception:
            return None

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
                "max_instances": 1,
            },
        ]
