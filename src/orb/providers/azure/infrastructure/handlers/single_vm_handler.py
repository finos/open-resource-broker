"""SingleVM Handler - provisions individual VMs via the Azure Compute SDK.

This handler is used when ``provider_api == "SingleVM"`` in the template.
It creates standalone Virtual Machines rather than VMSS instances, which
is suitable for long-lived singleton workloads.
"""

from __future__ import annotations
import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Optional, cast

from orb.domain.base.dependency_injection import injectable
from orb.domain.request.aggregate import Request
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
from orb.providers.azure.infrastructure.sdk_shapes import (
    AzureVmRuntimeStatusProtocol,
    AzureVmWithIdentityProtocol,
    instance_view_statuses,
)
from orb.providers.azure.infrastructure.handlers._network_identity import (
    resolve_network_identity_or_empty_async,
)
from orb.providers.azure.infrastructure.handlers.azure_status import resolve_power_state
from orb.providers.azure.infrastructure.handlers.azure_handler import (
    AzureAcquireHostsResult,
    AzureHandler,
    AzureReleaseContext,
    AzureSubmittedDeletion,
    AzureHandlerStatusResult,
    AzureReleaseHostsResult,
    AzureSingleVmReleaseProviderData,
    AzureStatusProviderData,
    azure_raise_on_status_error,
)
from orb.providers.azure.infrastructure.services.azure_network_identity_resolver import (
    AzureNetworkIdentity,
)

if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort
    from orb.providers.azure.infrastructure.azure_client import AzureClient
    from orb.providers.azure.infrastructure.services.azure_native_spec_service import (
        AzureNativeSpecService,
    )


def _azure_resource_not_found_error_type() -> type[Exception]:
    """Resolve the Azure SDK's not-found exception lazily."""
    from azure.core.exceptions import ResourceNotFoundError

    return ResourceNotFoundError


def _primary_error_message(error_details: dict[str, Any]) -> str:
    return str(
        error_details.get("error_message")
        or error_details.get("message")
        or "Failed to submit SingleVM deployment"
    )


def _nested_error_messages(error_details: dict[str, Any]) -> list[str]:
    nested_details = error_details.get("details")
    if not isinstance(nested_details, list):
        return []

    messages: list[str] = []
    for detail in nested_details:
        if not isinstance(detail, dict):
            continue
        message = detail.get("message")
        if message in (None, ""):
            continue
        code = detail.get("code")
        messages.append(f"{code}: {message}" if code not in (None, "") else str(message))
    return messages


def _format_azure_error_message(error_details: dict[str, Any]) -> str:
    """Return the most actionable Azure create failure message."""
    nested_messages = _nested_error_messages(error_details)
    if nested_messages:
        return "; ".join(nested_messages)
    return _primary_error_message(error_details)


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _build_vm_name_lookup(vms: Iterable[AzureVmWithIdentityProtocol]) -> dict[str, str]:
    """Build a lookup that resolves VM names and VM IDs to VM names."""
    lookup: dict[str, str] = {}
    for vm in vms:
        vm_name = vm.name
        if not vm_name:
            continue
        resolved_name = str(vm_name)
        lookup[resolved_name] = resolved_name

        vm_id = vm.vm_id
        if vm_id:
            lookup[str(vm_id)] = resolved_name
    return lookup


@injectable
class SingleVMHandler(AzureHandler):
    """Handler that creates and manages individual Azure VMs.

    ``provider_api = "SingleVM"``
    """

    def __init__(
        self,
        azure_client: AzureClient,
        logger: LoggingPort,
        *,
        azure_native_spec_service: AzureNativeSpecService | None = None,
    ) -> None:
        """Initialize handler with deployment and optional native-spec service."""
        super().__init__(azure_client=azure_client, logger=logger)
        from orb.providers.azure.infrastructure.services.azure_deployment_service import (
            AzureDeploymentService,
        )

        self.azure_deployment_service = AzureDeploymentService(
            azure_client=self.azure_client,
            logger=self._logger,
        )
        self.azure_native_spec_service = azure_native_spec_service

    @staticmethod
    def _build_status_result(
        *,
        vm: AzureVmRuntimeStatusProtocol,
        resource_group: str,
        status: str,
        network_identity: AzureNetworkIdentity,
    ) -> AzureHandlerStatusResult:
        """Build a typed status result for one Azure VM."""
        hw = vm.hardware_profile
        availability_zone = vm.zones[0] if vm.zones else None
        provider_data: AzureStatusProviderData = {
            "resource_id": str(vm.name),
            "cloud_host_id": vm.vm_id or vm.name,
            "vm_name": str(vm.name),
            "vm_id": str(vm.vm_id),
            "resource_group": resource_group,
            "location": str(vm.location),
            "availability_zone": availability_zone,
            "nic_id": network_identity["nic_id"],
            "nic_name": network_identity["nic_name"],
            "vnet_id": network_identity["vnet_id"],
        }
        return {
            "instance_id": str(vm.name),
            "name": str(vm.name),
            "status": status,
            "private_ip": network_identity["private_ip"],
            "public_ip": network_identity["public_ip"],
            "launch_time": None,
            "instance_type": str(hw.vm_size) if hw and hw.vm_size else None,
            "subnet_id": network_identity["subnet_id"],
            "vpc_id": network_identity["vnet_id"],
            "tags": vm.tags or {},
            "price_type": None,
            "provider_type": "azure",
            "provider_data": provider_data,
        }

    async def acquire_hosts_async(
        self, request: Request, template: AzureTemplate
    ) -> AzureAcquireHostsResult:
        """Async create for individual VMs using async ARM deployment submission."""
        resource_group = template.resource_group.value
        location = template.location.value
        count = request.requested_count

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

        nsg_id = (
            template.network_config.network_security_group_id if template.network_config else None
        )
        accel_net = bool(
            template.network_config.accelerated_networking if template.network_config else False
        )
        backend_pool_ids = (
            template.network_config.load_balancer_backend_pool_ids
            if template.network_config
            else []
        )
        inbound_nat_pool_ids = (
            template.network_config.load_balancer_inbound_nat_pool_ids
            if template.network_config
            else []
        )
        app_gateway_pool_ids = (
            template.network_config.application_gateway_backend_pool_ids
            if template.network_config
            else []
        )
        public_ip_enabled = bool(
            template.network_config.public_ip_enabled if template.network_config else False
        )

        resolved_ssh_keys = list(template.ssh_public_keys)
        if template.ssh_key_name and not resolved_ssh_keys:
            from orb.providers.azure.infrastructure.services.ssh_key_resolver import (
                AzureComputeSshKeyClientProtocol,
                resolve_ssh_keys_async,
            )

            resolved_ssh_keys = await resolve_ssh_keys_async(
                ssh_key_name=template.ssh_key_name,
                ssh_public_keys=template.ssh_public_keys,
                resource_group=template.resource_group.value,
                # cast: SDK's SshPublicKeyResource structurally satisfies our
                # AzureSshPublicKeyResourceProtocol, but pyright requires
                # invariance through the Awaitable wrapper on the operations
                # protocol — so the inferred return type differs even though
                # the concrete shapes match.
                compute_client=cast(
                    AzureComputeSshKeyClientProtocol,
                    await self.azure_client.get_async_compute_client(),
                ),
            )
        if resolved_ssh_keys != list(template.ssh_public_keys):
            template = template.model_copy(update={"ssh_public_keys": resolved_ssh_keys})

        candidate_vm_sizes = template.candidate_vm_sizes
        vm_definitions: list[dict[str, Any]] = []
        for _ in range(count):
            vm_name = f"vm-{template.template_id}-{uuid.uuid4().hex[:8]}"
            vm_definitions.append(
                {
                    "vm_name": vm_name,
                    "nic_name": f"nic-{vm_name}",
                    "public_ip_name": f"pip-{vm_name}" if public_ip_enabled else None,
                }
            )

        selected_vm_size: Optional[str] = None
        submitted_deployment_name: Optional[str] = None
        last_error_details: dict[str, Any] = {}
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
                    resolved_vm_definitions.append({**vm_definition, "vm_payload": vm_params})

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
                submitted_deployment_name = (
                    await self.azure_deployment_service.submit_template_deployment_async(
                        resource_group=resource_group,
                        deployment_name=deployment_name,
                        template=deployment_template,
                    )
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
                    "details": error_details["details"],
                }
                if not self._is_capacity_error(exc):
                    break

        if submitted_deployment_name is None or selected_vm_size is None:
            raise LaunchError(
                message=_format_azure_error_message(last_error_details),
                template_id=template.template_id,
                error_code=last_error_details.get("error_code"),
                details=last_error_details,
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
                "fulfillment_final": True,
                "deployment_name": submitted_deployment_name,
                "error_codes": [],
                "fleet_errors": [],
                "submitted_vms": operation_tracking,
            },
        }

    async def check_hosts_status_async(self, request: Request) -> list[AzureHandlerStatusResult]:
        """Async status query for individual VM IDs using the Azure async Compute SDK."""
        resource_ids: list[str] = request.resource_ids or []
        raise_on_status_error = azure_raise_on_status_error(request)
        resource_group = (request.metadata or {}).get(
            "resource_group"
        ) or self.azure_client.resource_group
        if not resource_group:
            message = "Cannot resolve resource_group for status check"
            self._logger.error(message)
            if resource_ids:
                raise RuntimeError(message)
            return []

        results: list[AzureHandlerStatusResult] = []
        status_errors: list[str] = []
        compute = await self.azure_client.get_async_compute_client()
        resolved_vm_names = await self._resolve_vm_names_async(resource_group, resource_ids)

        for vm_name in resolved_vm_names:
            try:
                vm = await compute.virtual_machines.get(
                    resource_group_name=resource_group,
                    vm_name=vm_name,
                    expand="instanceView",
                )
                network_identity = await resolve_network_identity_or_empty_async(
                    logger=self._logger,
                    target_label=f"VM '{vm_name}'",
                    resolver=lambda: self.azure_client.resolve_network_identity_from_vm_async(vm),
                )
                statuses = instance_view_statuses(vm.instance_view)
                results.append(
                    self._build_status_result(
                        # cast: SDK 38 exposes hardware_profile/instance_view/vm_id
                        # via __flattened_items __getattr__, invisible to static
                        # checkers though documented as the SDK's public surface.
                        vm=cast(AzureVmRuntimeStatusProtocol, vm),
                        resource_group=resource_group,
                        status=(
                            resolve_power_state(statuses) if statuses is not None else "unknown"
                        ),
                        network_identity=network_identity,
                    )
                )
            except Exception as exc:
                error_message = f"Failed to get status for VM '{vm_name}': {exc}"
                self._logger.error(error_message)
                status_errors.append(error_message)
        all_requested_vms_failed = bool(resolved_vm_names) and not results
        if status_errors and (raise_on_status_error or all_requested_vms_failed):
            raise RuntimeError("; ".join(status_errors))
        return results

    @staticmethod
    def _raise_release_failures(
        *,
        machine_ids: list[str],
        resource_group: str,
        submitted_deletions: list[AzureSubmittedDeletion],
        failed_deletions: list[AzureSubmittedDeletion],
    ) -> None:
        """Raise an aggregated termination error when any delete submissions fail."""
        if not failed_deletions:
            return

        failed_requested_ids = [
            requested_id
            for deletion in failed_deletions
            if (requested_id := deletion.get("requested_id")) is not None
        ]
        raise TerminationError(
            (f"Failed to submit deletion for {len(failed_deletions)} of {len(machine_ids)} VM(s)"),
            resource_ids=failed_requested_ids,
            details={
                "resource_group": resource_group,
                "submitted_deletions": submitted_deletions,
                "failed_deletions": failed_deletions,
            },
        )

    @staticmethod
    def _build_release_result(
        resource_group: str,
        submitted_deletions: list[AzureSubmittedDeletion],
    ) -> AzureReleaseHostsResult:
        """Build the typed release result payload for SingleVM deletes."""
        provider_data: AzureSingleVmReleaseProviderData = {
            "resource_group": resource_group,
            "operation_status": "submitted",
            "submitted_deletions": submitted_deletions,
        }
        return {"provider_data": provider_data}

    async def release_hosts_async(
        self,
        machine_ids: list[str],
        resource_id: str,
        context: Optional[AzureReleaseContext] = None,
    ) -> Optional[AzureReleaseHostsResult]:
        """Async delete submission for individual VMs using the Azure async Compute SDK."""
        resource_group = self._resolve_release_resource_group(
            context=context,
            machine_ids=machine_ids,
        )
        compute = await self.azure_client.get_async_compute_client()
        vm_names = await self._resolve_vm_names_async(resource_group, machine_ids)
        submitted_deletions: list[AzureSubmittedDeletion] = []
        failed_deletions: list[AzureSubmittedDeletion] = []
        for original_id, vm_name in zip(machine_ids, vm_names, strict=True):
            try:
                self._logger.info("Deleting VM '%s' (requested id='%s')", vm_name, original_id)
                await compute.virtual_machines.begin_delete(
                    resource_group_name=resource_group,
                    vm_name=vm_name,
                )
                submitted_deletions.append({"requested_id": str(original_id), "vm_name": vm_name})
            except Exception as exc:
                self._logger.error("Failed to delete VM '%s': %s", vm_name, exc)
                failed_deletions.append(
                    {"requested_id": str(original_id), "vm_name": vm_name, "error": str(exc)}
                )

        self._raise_release_failures(
            machine_ids=machine_ids,
            resource_group=resource_group,
            submitted_deletions=submitted_deletions,
            failed_deletions=failed_deletions,
        )
        return self._build_release_result(resource_group, submitted_deletions)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _finalize_resolved_vm_names(
        *,
        resource_group: str,
        machine_ids: list[str],
        resolved: list[Optional[str]],
        logger: LoggingPort,
    ) -> list[str]:
        """Apply unresolved lookups, preserve input order, and log any remapping."""
        ordered_resolved = [
            resolved_name if resolved_name is not None else str(machine_id)
            for machine_id, resolved_name in zip(machine_ids, resolved, strict=True)
        ]
        if ordered_resolved != [str(mid) for mid in machine_ids]:
            logger.debug(
                "Resolved SingleVM IDs in resource_group '%s': %s -> %s",
                resource_group,
                machine_ids,
                ordered_resolved,
            )
        return ordered_resolved

    async def _resolve_vm_names_async(
        self, resource_group: str, machine_ids: list[str]
    ) -> list[str]:
        """Resolve a list of mixed identifiers to canonical Azure VM names.

        Each input is treated as a vm_name first and looked up via a direct
        ``virtual_machines.get`` (cheap when callers already have names). Inputs
        that look like Azure ``vm_id`` GUIDs, or that 404 on the direct lookup,
        are deferred to a single ``virtual_machines.list`` over the resource
        group and matched by vm_id. The output preserves input order.

        Best-effort: any unhandled error falls back to returning the input list
        as-is so callers can still attempt downstream operations with the
        original identifiers.
        """
        if not machine_ids:
            return []

        try:
            compute = await self.azure_client.get_async_compute_client()
            resolved: list[Optional[str]] = [None] * len(machine_ids)
            unresolved_indices: list[int] = []

            for index, machine_id in enumerate(machine_ids):
                machine_id_str = str(machine_id)
                if _looks_like_uuid(machine_id_str):
                    unresolved_indices.append(index)
                    continue

                try:
                    vm = await compute.virtual_machines.get(
                        resource_group_name=resource_group,
                        vm_name=machine_id_str,
                    )
                    resolved[index] = str(vm.name or machine_id_str)
                except _azure_resource_not_found_error_type():
                    unresolved_indices.append(index)

            if unresolved_indices:
                pager = compute.virtual_machines.list(resource_group_name=resource_group)
                # cast: SDK 38 exposes vm_id via __flattened_items __getattr__,
                # invisible to static checkers though documented as the SDK's
                # public surface.
                lookup = _build_vm_name_lookup(
                    [cast(AzureVmWithIdentityProtocol, vm) async for vm in pager]
                )

                for index in unresolved_indices:
                    machine_id = str(machine_ids[index])
                    resolved[index] = lookup.get(machine_id, machine_id)

            return self._finalize_resolved_vm_names(
                resource_group=resource_group,
                machine_ids=machine_ids,
                resolved=resolved,
                logger=self._logger,
            )
        except Exception as exc:
            self._logger.warning(
                "Failed to resolve VM names, using provided IDs directly: %s",
                exc,
            )
            return [str(machine_id) for machine_id in machine_ids]

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
        """Return example SingleVM template configurations."""
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
