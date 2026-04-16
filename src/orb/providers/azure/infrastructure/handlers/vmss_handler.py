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
  requested, so VMSS termination in this provider uses exact-instance deletion
  and, when needed, a narrow follow-up that deletes the VMSS after it is empty.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, cast

from orb.domain.base.dependency_injection import injectable
from orb.domain.request.aggregate import Request
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
from orb.providers.azure.infrastructure.sdk_shapes import (
    AzureVmWithIdentityProtocol,
    instance_view_statuses,
)
from orb.providers.azure.infrastructure.handlers._network_identity import (
    empty_network_identity,
    network_identity_soft_failure_types,
)
from orb.providers.azure.infrastructure.vmss_cleanup import PendingVmssCleanup
from orb.providers.infrastructure.error_codes import ProviderErrorEntry
from orb.providers.azure.infrastructure.handlers.azure_status import resolve_power_state
from orb.providers.azure.infrastructure.handlers.azure_handler import (
    AzureAcquireHostsResult,
    AzureHandler,
    AzureReleaseContext,
    AzureHandlerStatusResult,
    AzureReleaseHostsResult,
)
from orb.providers.azure.domain.template.value_objects import AzureVMSSOrchestrationMode

if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort
    from orb.providers.azure.infrastructure.azure_client import AzureClient
    from orb.providers.azure.infrastructure.services.azure_native_spec_service import (
        AzureNativeSpecService,
    )
    from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager


def _status_attr(status: Any, attr: str, default: Any = None) -> Any:
    """Read Azure status attributes from SDK objects or attribute-based test doubles.

    getattr is necessary here: callers pass heterogeneous status-like objects
    (SDK InstanceViewStatus, plain dicts wrapped in SimpleNamespace, etc.) and
    the requested attribute varies per call-site.
    """
    return getattr(status, attr, default)


@dataclass(frozen=True)
class _AzureVmIdentity:
    """Normalized identity fields across Azure VM and VMSS VM SDK shapes."""

    instance_id: str
    vm_id: str
    vm_name: Optional[str]


def _build_vmss_delete_instance_ids(instance_ids: list[str]) -> Any:
    """Build the VMSS delete payload using the SDK model when available."""
    try:
        from azure.mgmt.compute.models import VirtualMachineScaleSetVMInstanceRequiredIDs
    except ImportError:
        return {"instance_ids": instance_ids}
    return VirtualMachineScaleSetVMInstanceRequiredIDs(instance_ids=instance_ids)


def _read_vm_identity(vm: Any) -> _AzureVmIdentity:
    """Normalize VM identity across VMSS VM and regular VM SDK objects.

    Microsoft documents `VirtualMachineScaleSetVM` with `name`, `instance_id`,
    and `vm_id`, while regular `VirtualMachine` objects expose `name` and
    `vm_id` but not `instance_id`. When Azure returns a regular VM object,
    `name` becomes the stable machine identifier when `instance_id` is absent.
    """
    typed_vm = cast(AzureVmWithIdentityProtocol, vm)
    vm_name = typed_vm.name
    instance_id = str(_status_attr(vm, "instance_id", "") or vm_name or "")
    vm_id = str(typed_vm.vm_id or instance_id)
    return _AzureVmIdentity(
        instance_id=instance_id,
        vm_id=vm_id,
        vm_name=vm_name,
    )


@injectable
class VMSSHandler(AzureHandler):
    """Handler that creates and manages VMSS resources.

    ``provider_api = "VMSS"`` or ``"VMSSUniform"``
    """

    def __init__(
        self,
        azure_client: "AzureClient",
        logger: "LoggingPort",
        *,
        azure_native_spec_service: "AzureNativeSpecService | None" = None,
        azure_resource_manager: "AzureResourceManager | None" = None,
    ) -> None:
        """Initialize handler with explicit optional infrastructure services."""
        super().__init__(azure_client=azure_client, logger=logger)
        self.azure_native_spec_service = azure_native_spec_service
        self.azure_resource_manager = azure_resource_manager

    # ------------------------------------------------------------------
    # acquire_hosts
    # ------------------------------------------------------------------

    def acquire_hosts(
        self, request: Request, template: AzureTemplate
    ) -> AzureAcquireHostsResult:
        """Create a VMSS and return the result.

        The VMSS is created using the ARM payload generated by
        ``ArmPayloadMapper.vmss_payload()``. The create LRO is submitted
        and the VMSS name is returned immediately; instance discovery happens
        later via status checks.
        """
        vmss_name = template.vmss_name or f"vmss-{template.template_id}-{uuid.uuid4().hex[:8]}"
        resource_group = template.resource_group.value
        location = template.location.value

        self._logger.info(
            "Creating VMSS '%s' in resource group '%s' (location=%s, capacity=%d)",
            vmss_name,
            resource_group,
            location,
            request.requested_count,
        )

        try:
            resolved_template = template
            # Resolve subnet_id if network_config is missing
            if not resolved_template.network_config:
                subnet_id = None
                if resolved_template.subnet_ids:
                    candidate = resolved_template.subnet_ids[0]
                    if candidate and candidate != "default-subnet":
                        subnet_id = candidate

                if subnet_id:
                    # Build a local template copy so subnet normalization does
                    # not mutate the original aggregate across repeated calls.
                    from orb.providers.azure.domain.template.value_objects import (
                        AzureNetworkConfig,
                    )

                    resolved_template = resolved_template.model_copy(
                        update={"network_config": AzureNetworkConfig(subnet_id=subnet_id)}
                    )
                    self._logger.debug("Auto-created network_config from subnet_id: %s", subnet_id)
                else:
                    raise AzureValidationError(
                        "No subnet specified. Add 'subnet_id' (full ARM resource ID) "
                        "to the template in subnet_ids or network_config, e.g.: "
                        "/subscriptions/<sub>/resourceGroups/<rg>/providers/"
                        "Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>",
                        details={"template_id": template.template_id},
                        error_code="InvalidParameter",
                    )

            from orb.providers.azure.infrastructure.services.arm_payload_mapper import ArmPayloadMapper

            arm_payload = ArmPayloadMapper.vmss_payload(resolved_template)

            if self.azure_native_spec_service:
                merged_payload = self.azure_native_spec_service.process_provider_api_spec_with_merge(
                    template=resolved_template,
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
            # happen after the ARM payload has been built so the resolved
            # keys end up in the payload's osProfile.
            if resolved_template.ssh_key_name and not resolved_template.ssh_public_keys:
                from orb.providers.azure.infrastructure.services.ssh_key_resolver import (
                    resolve_ssh_keys,
                )

                resolved_keys = resolve_ssh_keys(
                    ssh_key_name=resolved_template.ssh_key_name,
                    ssh_public_keys=resolved_template.ssh_public_keys,
                    resource_group=resolved_template.resource_group.value,
                    compute_client=compute,
                )
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
                    "error_codes": [],
                    # Azure async create returns resource tracking first and instances later.
                    # Mark the submit attempt as final so generic top-up retry logic does not
                    # reissue the same create request and duplicate resources.
                    "fulfillment_final": True,
                },
            }

        except AzureValidationError:
            raise
        except Exception as exc:
            error_msg = f"Failed to create VMSS '{vmss_name}': {exc}"
            self._logger.error(error_msg)

            # Translate Azure SDK errors into domain exceptions where possible,
            # preserving the canonical error code so spot placement retry logic
            # can classify capacity-like failures.
            error_code = canonical_azure_error_code(exc)
            error_details = extract_azure_error_details(exc)
            error_str = str(error_details.get("message") or "").lower()
            if error_code in {"QuotaExceeded", "OperationNotAllowed", "ResourceQuotaExceeded"} or (
                "quota" in error_str or "exceeded" in error_str
            ):
                raise QuotaExceededError(error_msg, error_code=error_code) from exc
            if error_code in {"InvalidRequest", "InvalidParameter", "BadRequest"} or (
                "validation" in error_str or "invalid" in error_str
            ):
                raise AzureValidationError(error_msg, error_code=error_code) from exc

            raise VMSSCreationError(
                message=error_msg,
                template_id=template.template_id,
                vmss_name=vmss_name,
                error_code=error_code,
            ) from exc

    # ------------------------------------------------------------------
    # check_hosts_status
    # ------------------------------------------------------------------

    def check_hosts_status(self, request: Request) -> list[AzureHandlerStatusResult]:
        """Return instance details for every VM in the VMSS(es)."""
        resource_ids = request.resource_ids
        if not resource_ids:
            self._logger.warning("check_hosts_status called with no resource_ids")
            return []

        all_instances: list[AzureHandlerStatusResult] = []
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
        context: Optional[AzureReleaseContext] = None,
    ) -> Optional[AzureReleaseHostsResult]:
        """Delete specific VM instances from a VMSS."""
        release_context = context or AzureReleaseContext()
        resource_group = release_context.resource_group or self.azure_client.resource_group
        if not resource_group:
            raise TerminationError(
                "resource_group is required for release_hosts",
                resource_ids=machine_ids,
            )

        vmss_name = resource_id
        compute = self.azure_client.compute_client

        if not machine_ids:
            return None

        orchestration_mode = self._get_vmss_orchestration_mode(resource_group, vmss_name)
        current_members = self._list_vmss_instances(
            resource_group=resource_group,
            vmss_name=vmss_name,
            include_instance_view=False,
            orchestration_mode=orchestration_mode,
        )
        resolved_instance_ids = (
            self._resolve_vmss_instance_ids(
                resource_group=resource_group,
                vmss_name=vmss_name,
                machine_ids=machine_ids,
                current_members=current_members,
            )
            if orchestration_mode != AzureVMSSOrchestrationMode.FLEXIBLE
            else [str(machine_id) for machine_id in machine_ids]
        )

        delete_vmss_when_empty = self._should_delete_vmss_when_empty(
            resource_group=resource_group,
            vmss_name=vmss_name,
            orchestration_mode=orchestration_mode,
            machine_ids=machine_ids,
            current_members=current_members,
            resolved_instance_ids=resolved_instance_ids,
        )

        self._logger.info(
            "Deleting %d instance(s) from VMSS '%s'",
            len(machine_ids),
            vmss_name,
        )
        try:
            cleanup_submission_state = {
                "delete_submitted": False,
                "delete_retry_pending": False,
                "last_delete_error": None,
            }
            if orchestration_mode == AzureVMSSOrchestrationMode.FLEXIBLE:
                submitted_deletions: list[dict[str, Any]] = []
                for vm_name in machine_ids:
                    # Fire-and-forget: deletion is async; the caller
                    # reconciles completion via check_hosts_status polling.
                    compute.virtual_machines.begin_delete(
                        resource_group_name=resource_group,
                        vm_name=str(vm_name),
                    )
                    submitted_deletions.append({"vm_name": str(vm_name)})
                self._logger.info(
                    "Submitted delete for %d flexible VMSS instance(s) from '%s'",
                    len(machine_ids),
                    vmss_name,
                )
                if delete_vmss_when_empty:
                    # Member deletes have already been submitted above. If the
                    # follow-up VMSS delete submission fails, Azure will expose
                    # that intermediate state until async cleanup retries it.
                    cleanup_submission_state = self._submit_vmss_delete_if_emptying(
                        resource_group=resource_group,
                        vmss_name=vmss_name,
                    )
                return {
                    "provider_data": {
                        "resource_group": resource_group,
                        "vmss_name": vmss_name,
                        "operation_status": "submitted",
                        "submitted_deletions": submitted_deletions,
                        **self._release_cleanup_provider_data(
                            resource_group=resource_group,
                            vmss_name=vmss_name,
                            machine_ids=machine_ids,
                            delete_vmss_when_empty=delete_vmss_when_empty,
                            member_delete_submitted=True,
                            delete_submitted=cleanup_submission_state["delete_submitted"],
                            delete_retry_pending=cleanup_submission_state["delete_retry_pending"],
                            last_delete_error=cleanup_submission_state["last_delete_error"],
                        ),
                    }
                }

            # Fire-and-forget: deletion is async; the caller
            # reconciles completion via check_hosts_status polling.
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
            if delete_vmss_when_empty:
                cleanup_submission_state = self._submit_vmss_delete_if_emptying(
                    resource_group=resource_group,
                    vmss_name=vmss_name,
                )
            return {
                "provider_data": {
                    "resource_group": resource_group,
                    "vmss_name": vmss_name,
                    "operation_status": "submitted",
                    "resolved_instance_ids": resolved_instance_ids,
                    **self._release_cleanup_provider_data(
                        resource_group=resource_group,
                        vmss_name=vmss_name,
                        machine_ids=machine_ids,
                        delete_vmss_when_empty=delete_vmss_when_empty,
                        member_delete_submitted=True,
                        delete_submitted=cleanup_submission_state["delete_submitted"],
                        delete_retry_pending=cleanup_submission_state["delete_retry_pending"],
                        last_delete_error=cleanup_submission_state["last_delete_error"],
                    ),
                }
            }
        except Exception as exc:
            raise TerminationError(
                f"Failed to delete instances from VMSS '{vmss_name}': {exc}",
                resource_ids=machine_ids,
            ) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_pending_resource_cleanup(
        *,
        resource_group: str,
        vmss_name: str,
        machine_ids: list[str],
        member_delete_submitted: bool = False,
        delete_submitted: bool = False,
        delete_retry_pending: bool = False,
        last_delete_error: Optional[str] = None,
    ) -> dict[str, Any]:
        return PendingVmssCleanup.create(
            resource_group=resource_group,
            vmss_name=vmss_name,
            machine_ids=machine_ids,
            delete_vmss_when_empty=True,
            member_delete_submitted=member_delete_submitted,
            delete_submitted=delete_submitted,
            delete_retry_pending=delete_retry_pending,
            last_delete_error=last_delete_error,
        ).to_metadata()

    def _release_cleanup_provider_data(
        self,
        *,
        resource_group: str,
        vmss_name: str,
        machine_ids: list[str],
        delete_vmss_when_empty: bool,
        member_delete_submitted: bool = False,
        delete_submitted: bool = False,
        delete_retry_pending: bool = False,
        last_delete_error: Optional[str] = None,
    ) -> dict[str, Any]:
        if not delete_vmss_when_empty:
            return {}

        return {
            "pending_resource_cleanup": self._build_pending_resource_cleanup(
                resource_group=resource_group,
                vmss_name=vmss_name,
                machine_ids=machine_ids,
                member_delete_submitted=member_delete_submitted,
                delete_submitted=delete_submitted,
                delete_retry_pending=delete_retry_pending,
                last_delete_error=last_delete_error,
            )
        }

    def _submit_vmss_delete_if_emptying(
        self,
        *,
        resource_group: str,
        vmss_name: str,
    ) -> dict[str, Any]:
        """Best-effort VMSS delete submission when this return should empty the scale set."""
        try:
            self.azure_client.compute_client.virtual_machine_scale_sets.begin_delete(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
            )
            self._logger.info(
                "Submitted immediate delete for VMSS '%s' because return should empty it",
                vmss_name,
            )
            return {
                "delete_submitted": True,
                "delete_retry_pending": False,
                "last_delete_error": None,
            }
        except Exception as exc:
            self._logger.info(
                "Immediate delete submission for VMSS '%s' did not succeed; async cleanup will retry: %s",
                vmss_name,
                exc,
            )
            return {
                "delete_submitted": False,
                "delete_retry_pending": True,
                "last_delete_error": str(exc),
            }

    def _should_delete_vmss_when_empty(
        self,
        *,
        resource_group: str,
        vmss_name: str,
        orchestration_mode: AzureVMSSOrchestrationMode,
        machine_ids: list[str],
        current_members: Optional[list[AzureHandlerStatusResult]] = None,
        resolved_instance_ids: Optional[list[str]] = None,
    ) -> bool:
        """Return whether deleting these exact members would leave the VMSS empty."""
        if not machine_ids:
            return False

        if current_members is None:
            current_members = self._list_vmss_instances(
                resource_group=resource_group,
                vmss_name=vmss_name,
                include_instance_view=False,
                orchestration_mode=orchestration_mode,
            )
        if not current_members:
            return False

        current_member_ids = {
            str(instance_id)
            for instance_id in (
                member.get("instance_id")
                for member in current_members
                if isinstance(member, dict)
            )
            if instance_id not in (None, "")
        }
        if len(current_member_ids) != len(current_members):
            return False

        if orchestration_mode == AzureVMSSOrchestrationMode.FLEXIBLE:
            requested_ids = {str(machine_id) for machine_id in machine_ids if machine_id not in (None, "")}
            return bool(requested_ids) and requested_ids == current_member_ids

        if resolved_instance_ids is None:
            resolved_instance_ids = self._resolve_vmss_instance_ids(
                resource_group=resource_group,
                vmss_name=vmss_name,
                machine_ids=machine_ids,
                current_members=current_members,
            )
        requested_ids = {
            str(instance_id)
            for instance_id in resolved_instance_ids
            if instance_id not in (None, "")
        }
        return bool(requested_ids) and requested_ids == current_member_ids

    def _resolve_vmss_instance_ids(
        self,
        resource_group: str,
        vmss_name: str,
        machine_ids: list[str],
        current_members: Optional[list[AzureHandlerStatusResult]] = None,
    ) -> list[str]:
        """Resolve mixed IDs (vm_id/vm_name/instance_id) to VMSS instance IDs."""
        if not machine_ids:
            return []

        resolved: list[str] = []

        try:
            lookup: dict[str, str] = {}
            if current_members is None:
                current_members = self._list_vmss_instances(
                    resource_group=resource_group,
                    vmss_name=vmss_name,
                    include_instance_view=False,
                    orchestration_mode=AzureVMSSOrchestrationMode.UNIFORM,
                )

            for vm in current_members:
                if not isinstance(vm, dict):
                    continue
                vmss_instance_id = str(vm.get("instance_id", "") or "")
                if not vmss_instance_id:
                    continue
                lookup[vmss_instance_id] = vmss_instance_id

                provider_data = vm.get("provider_data")
                vm_id = provider_data.get("vm_id") if isinstance(provider_data, dict) else None
                if vm_id:
                    lookup[str(vm_id)] = vmss_instance_id

                vm_name = provider_data.get("vm_name") if isinstance(provider_data, dict) else None
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
        orchestration_mode: Optional[AzureVMSSOrchestrationMode] = None,
    ) -> list[AzureHandlerStatusResult]:
        """Return a list of normalised instance dicts for a VMSS."""
        compute = self.azure_client.compute_client
        if orchestration_mode is None:
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

        instances: list[AzureHandlerStatusResult] = []
        for vm in vms:
            instance_data = self._normalise_vm(vm, vmss_name, resource_group)
            instances.append(instance_data)
        return instances

    def get_vmss_resource_errors(
        self,
        resource_group: str,
        vmss_name: str,
    ) -> list[ProviderErrorEntry]:
        """Return VMSS-level provisioning errors even when no instances are visible yet."""
        compute = self.azure_client.compute_client
        try:
            vmss = compute.virtual_machine_scale_sets.get(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
            )
        except Exception as exc:
            self._logger.warning(
                "Failed to fetch VMSS resource errors for '%s' in resource group '%s': %s",
                vmss_name,
                resource_group,
                exc,
            )
            return []

        errors: list[ProviderErrorEntry] = []
        provisioning_state = str(vmss.provisioning_state or "")
        # getattr: VirtualMachineScaleSet has no `statuses` attribute; it only
        # appears on the instance-view sub-resource which may not be expanded.
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
        raw_mode = vmss.orchestration_mode or "Flexible"
        return AzureVMSSOrchestrationMode(str(raw_mode))

    def _list_flexible_vmss_instances(
        self,
        resource_group: str,
        vmss_name: str,
        include_instance_view: bool = False,
    ) -> list[AzureHandlerStatusResult]:
        compute = self.azure_client.compute_client
        vmss_resource_id = (
            f"/subscriptions/{self.azure_client.subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Compute/virtualMachineScaleSets/{vmss_name}"
        )
        # Flexible VMSS membership is queried through the standard VM list API.
        # Azure rejects the VMSS child .../virtualMachines API for Flexible orchestration.
        list_kwargs: dict[str, Any] = {
            "resource_group_name": resource_group,
            "filter": f"'virtualMachineScaleSet/id' eq '{vmss_resource_id}'",
        }
        if include_instance_view:
            list_kwargs["expand"] = "instanceView"

        try:
            vms = list(compute.virtual_machines.list(**list_kwargs))
        except Exception as exc:
            raise VMSSNotFoundError(
                f"Could not list flexible VMs for VMSS '{vmss_name}': {exc}",
                vmss_name=vmss_name,
            ) from exc

        instances: list[AzureHandlerStatusResult] = []

        for vm in vms:
            instances.append(self._normalise_vm(vm, vmss_name, resource_group))

        return instances

    def _normalise_vm(
        self, vm: Any, vmss_name: str, resource_group: str
    ) -> AzureHandlerStatusResult:
        """Convert an Azure SDK VirtualMachineScaleSetVM to a normalised dict."""
        vm_identity = _read_vm_identity(vm)

        # Extract status
        status = "unknown"
        instance_view = vm.instance_view
        vm_statuses = instance_view_statuses(instance_view)
        if vm_statuses is not None:
            status = resolve_power_state(vm_statuses)
            fleet_errors = self._extract_vm_errors(
                vm_statuses,
                instance_id=vm_identity.instance_id,
                vmss_name=vmss_name,
            )
        else:
            fleet_errors = []

        # Network IPs / subnet / VNet
        network_identity = empty_network_identity()
        try:
            network_identity = self.azure_client.resolve_network_identity_from_vm(vm)
        except network_identity_soft_failure_types() as exc:
            # Optional NIC/IP enrichment must not hide an otherwise visible VMSS member.
            self._logger.warning(
                "Failed to resolve network identity for VMSS member '%s' in '%s': %s",
                vm_identity.instance_id,
                vmss_name,
                exc,
            )
        private_ip = network_identity["private_ip"]
        public_ip = network_identity["public_ip"]
        subnet_id = network_identity["subnet_id"]
        vnet_id = network_identity["vnet_id"]

        # Hardware profile → instance type
        hw = vm.hardware_profile
        instance_type = hw.vm_size if hw else None
        # Ensure instance_type is a valid non-empty string
        if not instance_type:
            instance_type = "unknown"

        # Location
        location = vm.location

        # Zones
        zones = vm.zones
        availability_zone = zones[0] if zones else None

        # Launch time approximation
        launch_time = None
        if vm_statuses is not None:
            for s in vm_statuses:
                t = s.time
                if t is not None:
                    launch_time = str(t)
                    break

        return {
            # Use VMSS instance_id as canonical machine id (Azure delete_instances expects this shape).
            "instance_id": vm_identity.instance_id,
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
                "vmss_instance_id": vm_identity.instance_id,
                "vm_id": vm_identity.vm_id,
                "vm_name": vm_identity.vm_name,
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
    ) -> list[ProviderErrorEntry]:
        """Extract provisioning failures from Azure instance view statuses."""
        errors: list[ProviderErrorEntry] = []

        for status in statuses:
            code = str(_status_attr(status, "code", "") or "")
            level = str(_status_attr(status, "level", "") or "")
            message = _status_attr(status, "message")
            display_status = _status_attr(status, "display_status")

            is_failure = code.lower().startswith("provisioningstate/failed") or level.lower() == "error"
            if not is_failure:
                continue

            vm_error: ProviderErrorEntry = {
                "error_code": "ProvisioningStateFailed",
                "error_message": str(message or display_status or code or "Azure provisioning failed"),
                "instance_id": instance_id,
                "resource_id": vmss_name,
                "status_code": code,
                "status_level": level or None,
            }
            errors.append(vm_error)

        return errors

    # ------------------------------------------------------------------
    # Example templates
    # ------------------------------------------------------------------

    @classmethod
    def get_example_templates(cls) -> list[dict[str, Any]]:
        """Return example VMSS template configurations."""
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
