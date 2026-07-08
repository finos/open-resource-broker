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
from typing import TYPE_CHECKING, Any, Optional, TypedDict, cast

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
    classify_azure_error,
)
from orb.providers.azure.infrastructure.sdk_shapes import (
    AzureVmWithIdentityProtocol,
    AzureVmRuntimeStatusProtocol,
    instance_view_statuses,
)
from orb.providers.azure.infrastructure.handlers._network_identity import (
    resolve_network_identity_or_empty_async,
)
from orb.providers.azure.infrastructure.vmss_cleanup import PendingVmssCleanup
from orb.providers.azure.infrastructure.error_codes import ProviderErrorEntry
from orb.providers.azure.infrastructure.handlers.azure_status import resolve_power_state
from orb.providers.azure.infrastructure.handlers.azure_handler import (
    AzureAcquireHostsResult,
    AzureHandler,
    AzureReleaseContext,
    AzureSubmittedDeletion,
    AzureHandlerStatusResult,
    AzureReleaseHostsResult,
    AzureStatusProviderData,
    AzureVmssReleaseProviderData,
    azure_raise_on_status_error,
)
from orb.providers.azure.infrastructure.services.azure_network_identity_resolver import (
    AzureNetworkIdentity,
)
from orb.providers.azure.domain.template.value_objects import AzureVMSSOrchestrationMode

if TYPE_CHECKING:
    from azure.mgmt.compute.models import OrchestrationMode as SdkOrchestrationMode

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


@dataclass(frozen=True)
class _VmssReleasePlan:
    """Precomputed VMSS member-release inputs for the async VMSS release flow."""

    resource_group: str
    vmss_name: str
    orchestration_mode: AzureVMSSOrchestrationMode
    current_members: list[AzureHandlerStatusResult]
    resolved_instance_ids: list[str]
    resolved_vm_names: list[str]
    delete_vmss_when_empty: bool


class _VmssCleanupSubmissionState(TypedDict):
    """Immediate VMSS delete-submission state carried into release provider data."""

    delete_submitted: bool
    delete_retry_pending: bool
    last_delete_error: Optional[str]


def _build_vmss_delete_instance_ids(instance_ids: list[str]) -> Any:
    """Build the VMSS delete payload using the SDK model when available."""
    try:
        from azure.mgmt.compute.models import VirtualMachineScaleSetVMInstanceRequiredIDs
    except ImportError:
        return {"instance_ids": instance_ids}
    return VirtualMachineScaleSetVMInstanceRequiredIDs(instance_ids=instance_ids)


def _read_vm_identity(vm: AzureVmWithIdentityProtocol) -> _AzureVmIdentity:
    """Normalize VM identity across VMSS VM and regular VM SDK objects.

    Microsoft documents `VirtualMachineScaleSetVM` with `name`, `instance_id`,
    and `vm_id`, while regular `VirtualMachine` objects expose `name` and
    `vm_id` but not `instance_id`. When Azure returns a regular VM object,
    `name` becomes the stable machine identifier when `instance_id` is absent.
    """
    vm_name = vm.name
    instance_id = str(_status_attr(vm, "instance_id", "") or vm_name or "")
    vm_id = str(vm.vm_id or instance_id)
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
        azure_client: AzureClient,
        logger: LoggingPort,
        *,
        azure_native_spec_service: AzureNativeSpecService | None = None,
        azure_resource_manager: AzureResourceManager | None = None,
    ) -> None:
        """Initialize handler with explicit optional infrastructure services."""
        super().__init__(azure_client=azure_client, logger=logger)
        self.azure_native_spec_service = azure_native_spec_service
        self.azure_resource_manager = azure_resource_manager

    async def acquire_hosts_async(
        self, request: Request, template: AzureTemplate
    ) -> AzureAcquireHostsResult:
        """Async VMSS create using the Azure async Compute SDK."""
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
            if not resolved_template.network_config:
                subnet_id = self._resolve_subnet_id(resolved_template)
                if subnet_id:
                    from orb.providers.azure.domain.template.value_objects import AzureNetworkConfig

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
            from orb.providers.azure.infrastructure.services.ssh_key_resolver import (
                AzureComputeSshKeyClientProtocol,
                resolve_ssh_keys_async,
            )

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

            arm_payload.setdefault("sku", {})
            arm_payload["sku"]["capacity"] = request.requested_count

            compute = await self.azure_client.get_async_compute_client()
            if resolved_template.ssh_key_name and not resolved_template.ssh_public_keys:
                resolved_keys = await resolve_ssh_keys_async(
                    ssh_key_name=resolved_template.ssh_key_name,
                    ssh_public_keys=resolved_template.ssh_public_keys,
                    resource_group=resolved_template.resource_group.value,
                    # cast: SDK's SshPublicKeyResource structurally satisfies our
                    # AzureSshPublicKeyResourceProtocol, but pyright requires
                    # invariance through the Awaitable wrapper on the operations
                    # protocol — so the inferred return type differs even though
                    # the concrete shapes match.
                    compute_client=cast(AzureComputeSshKeyClientProtocol, compute),
                )
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

            vmss_operations: Any = compute.virtual_machine_scale_sets
            await vmss_operations.begin_create_or_update(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
                parameters=arm_payload,
            )

            self._logger.info(
                "Submitted native VMSS create for '%s'; tracking will continue via status checks",
                vmss_name,
            )
            return {
                "success": True,
                "resource_ids": [vmss_name],
                "instances": [],
                "error_message": None,
                "provider_data": {
                    "vmss_name": vmss_name,
                    "resource_group": resource_group,
                    "location": location,
                    "provisioning_state": "creating",
                    "operation_status": "submitted",
                    "error_codes": [],
                    "fulfillment_final": True,
                },
            }
        except AzureValidationError:
            raise
        except Exception as exc:
            error_msg = f"Failed to create VMSS '{vmss_name}': {exc}"
            self._logger.error(error_msg)
            category, error_code = classify_azure_error(exc)
            if category == "quota":
                raise QuotaExceededError(error_msg, error_code=error_code) from exc
            if category == "validation":
                raise AzureValidationError(error_msg, error_code=error_code) from exc
            raise VMSSCreationError(
                message=error_msg,
                template_id=template.template_id,
                vmss_name=vmss_name,
                error_code=error_code,
            ) from exc

    async def check_hosts_status_async(self, request: Request) -> list[AzureHandlerStatusResult]:
        """Async status query for VMSS members using the Azure async Compute SDK."""
        resource_ids = request.resource_ids
        if not resource_ids:
            self._logger.warning("check_hosts_status called with no resource_ids")
            return []

        all_instances: list[AzureHandlerStatusResult] = []
        status_errors: list[str] = []
        raise_on_status_error = azure_raise_on_status_error(request)

        resource_group = (request.metadata or {}).get("resource_group") or self.azure_client.resource_group
        for vmss_name in resource_ids:
            if not resource_group:
                error_message = f"Cannot resolve resource_group for VMSS '{vmss_name}'"
                self._logger.error(error_message)
                status_errors.append(error_message)
                continue

            try:
                instances = await self._list_vmss_instances_async(
                    resource_group, vmss_name, include_instance_view=True
                )
                all_instances.extend(instances)
            except Exception as exc:
                error_message = f"Failed to list instances for VMSS '{vmss_name}': {exc}"
                self._logger.error(error_message)
                status_errors.append(error_message)

        all_requested_vmss_failed = bool(resource_ids) and not all_instances
        if status_errors and (raise_on_status_error or all_requested_vmss_failed):
            raise RuntimeError("; ".join(status_errors))

        return all_instances

    async def release_hosts_async(
        self,
        machine_ids: list[str],
        resource_id: str,
        context: Optional[AzureReleaseContext] = None,
    ) -> Optional[AzureReleaseHostsResult]:
        """Async delete submission for VMSS members using the Azure async Compute SDK."""
        if not machine_ids:
            return None

        release_plan = await self._build_release_plan_async(
            machine_ids=machine_ids,
            resource_id=resource_id,
            context=context,
        )
        resource_group = release_plan.resource_group
        vmss_name = release_plan.vmss_name
        compute = await self.azure_client.get_async_compute_client()

        self._log_release_submission(vmss_name=vmss_name, machine_ids=machine_ids)
        try:
            cleanup_submission_state: _VmssCleanupSubmissionState = {
                "delete_submitted": False,
                "delete_retry_pending": False,
                "last_delete_error": None,
            }
            if release_plan.orchestration_mode == AzureVMSSOrchestrationMode.FLEXIBLE:
                submitted_deletions: list[AzureSubmittedDeletion] = []
                failed_deletions: list[AzureSubmittedDeletion] = []
                for requested_id, vm_name in zip(machine_ids, release_plan.resolved_vm_names):
                    try:
                        await compute.virtual_machines.begin_delete(
                            resource_group_name=resource_group,
                            vm_name=str(vm_name),
                        )
                        submitted_deletions.append({
                            "requested_id": str(requested_id),
                            "vm_name": str(vm_name),
                        })
                    except Exception as exc:
                        self._logger.error(
                            "Failed to delete VMSS flexible member '%s' from '%s': %s",
                            vm_name,
                            vmss_name,
                            exc,
                        )
                        failed_deletions.append({
                            "requested_id": str(requested_id),
                            "vm_name": str(vm_name),
                            "error": str(exc),
                        })
                self._raise_flexible_release_failures(
                    machine_ids=machine_ids,
                    resource_group=resource_group,
                    vmss_name=vmss_name,
                    submitted_deletions=submitted_deletions,
                    failed_deletions=failed_deletions,
                )
                if release_plan.delete_vmss_when_empty:
                    cleanup_submission_state = await self._submit_vmss_delete_if_emptying_async(
                        resource_group=resource_group,
                        vmss_name=vmss_name,
                    )
                return self._build_flexible_release_result(
                    resource_group=resource_group,
                    vmss_name=vmss_name,
                    machine_ids=machine_ids,
                    delete_vmss_when_empty=release_plan.delete_vmss_when_empty,
                    cleanup_submission_state=cleanup_submission_state,
                    submitted_deletions=submitted_deletions,
                    failed_deletions=failed_deletions,
                )

            await compute.virtual_machine_scale_sets.begin_delete_instances(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
                vm_instance_i_ds=_build_vmss_delete_instance_ids(
                    release_plan.resolved_instance_ids
                ),
            )
            if release_plan.delete_vmss_when_empty:
                cleanup_submission_state = await self._submit_vmss_delete_if_emptying_async(
                    resource_group=resource_group,
                    vmss_name=vmss_name,
                )
            return self._build_uniform_release_result(
                resource_group=resource_group,
                vmss_name=vmss_name,
                machine_ids=machine_ids,
                delete_vmss_when_empty=release_plan.delete_vmss_when_empty,
                cleanup_submission_state=cleanup_submission_state,
                resolved_instance_ids=release_plan.resolved_instance_ids,
            )
        except TerminationError:
            raise
        except Exception as exc:
            raise TerminationError(
                f"Failed to delete instances from VMSS '{vmss_name}': {exc}",
                resource_ids=machine_ids,
            ) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log_release_submission(self, *, vmss_name: str, machine_ids: list[str]) -> None:
        """Log the start of a VMSS member-delete submission."""
        self._logger.info(
            "Deleting %d instance(s) from VMSS '%s'",
            len(machine_ids),
            vmss_name,
        )

    @staticmethod
    def _raise_flexible_release_failures(
        *,
        machine_ids: list[str],
        resource_group: str,
        vmss_name: str,
        submitted_deletions: list[AzureSubmittedDeletion],
        failed_deletions: list[AzureSubmittedDeletion],
    ) -> None:
        """Raise when any flexible VMSS member delete submission fails."""
        if not failed_deletions:
            return

        failed_requested_ids = [
            requested_id
            for deletion in failed_deletions
            if (requested_id := deletion.get("requested_id")) is not None
        ]
        raise TerminationError(
            (
                f"Failed to submit deletion for {len(failed_deletions)} of "
                f"{len(machine_ids)} VMSS member(s)"
            ),
            resource_ids=failed_requested_ids,
            details={
                "resource_group": resource_group,
                "vmss_name": vmss_name,
                "submitted_deletions": submitted_deletions,
                "failed_deletions": failed_deletions,
            },
        )

    async def _build_release_plan_async(
        self,
        *,
        machine_ids: list[str],
        resource_id: str,
        context: Optional[AzureReleaseContext],
    ) -> _VmssReleasePlan:
        """Precompute everything the release submission needs for one VMSS.

        Resolves the resource group, queries the VMSS to determine its
        orchestration mode, lists current members once, and produces the
        delete identifiers in the form the SDK expects for that mode:

        - Uniform VMSS deletes by ``instance_id`` — populated from
          ``current_members``; ``resolved_vm_names`` is empty.
        - Flexible VMSS deletes individual VMs by ``vm_name`` — populated
          from ``current_members``; ``resolved_instance_ids`` mirrors the
          input ``machine_ids`` (Flexible accepts either, but we forward
          the originals for traceability).

        Splitting plan-building from submission keeps the submission path
        flat and lets tests exercise the plan shape without mocking the
        delete calls.
        """
        resource_group = self._resolve_release_resource_group(
            machine_ids=machine_ids,
            context=context,
        )
        vmss_name = resource_id
        orchestration_mode = await self._get_vmss_orchestration_mode_async(
            resource_group, vmss_name
        )
        current_members = await self._list_vmss_instances_async(
            resource_group=resource_group,
            vmss_name=vmss_name,
            include_instance_view=False,
            orchestration_mode=orchestration_mode,
        )
        resolved_instance_ids = (
            await self._resolve_vmss_instance_ids_async(
                resource_group=resource_group,
                vmss_name=vmss_name,
                machine_ids=machine_ids,
                current_members=current_members,
            )
            if orchestration_mode != AzureVMSSOrchestrationMode.FLEXIBLE
            else [str(machine_id) for machine_id in machine_ids]
        )
        resolved_vm_names = (
            self._resolve_flexible_vm_names_from_members(
                machine_ids=machine_ids,
                current_members=current_members,
                logger=self._logger,
                vmss_name=vmss_name,
            )
            if orchestration_mode == AzureVMSSOrchestrationMode.FLEXIBLE
            else []
        )
        return _VmssReleasePlan(
            resource_group=resource_group,
            vmss_name=vmss_name,
            orchestration_mode=orchestration_mode,
            current_members=current_members,
            resolved_instance_ids=resolved_instance_ids,
            resolved_vm_names=resolved_vm_names,
            delete_vmss_when_empty=self._should_delete_vmss_when_empty(
                orchestration_mode=orchestration_mode,
                machine_ids=machine_ids,
                current_members=current_members,
                resolved_instance_ids=resolved_instance_ids,
                resolved_vm_names=resolved_vm_names,
            ),
        )

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
    ) -> AzureVmssReleaseProviderData:
        if not delete_vmss_when_empty:
            return {}

        return {
            "pending_resource_cleanup": cast(
                Any,
                self._build_pending_resource_cleanup(
                    resource_group=resource_group,
                    vmss_name=vmss_name,
                    machine_ids=machine_ids,
                    member_delete_submitted=member_delete_submitted,
                    delete_submitted=delete_submitted,
                    delete_retry_pending=delete_retry_pending,
                    last_delete_error=last_delete_error,
                ),
            )
        }

    def _build_uniform_release_result(
        self,
        *,
        resource_group: str,
        vmss_name: str,
        machine_ids: list[str],
        delete_vmss_when_empty: bool,
        cleanup_submission_state: _VmssCleanupSubmissionState,
        resolved_instance_ids: list[str],
    ) -> AzureReleaseHostsResult:
        """Build provider data for a uniform VMSS member-delete submission."""
        provider_data: AzureVmssReleaseProviderData = {
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
        return {"provider_data": provider_data}

    def _build_flexible_release_result(
        self,
        *,
        resource_group: str,
        vmss_name: str,
        machine_ids: list[str],
        delete_vmss_when_empty: bool,
        cleanup_submission_state: _VmssCleanupSubmissionState,
        submitted_deletions: list[AzureSubmittedDeletion],
        failed_deletions: list[AzureSubmittedDeletion],
    ) -> AzureReleaseHostsResult:
        """Build provider data for a flexible VMSS member-delete submission."""
        provider_data: AzureVmssReleaseProviderData = {
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
        if failed_deletions:
            provider_data["failed_deletions"] = failed_deletions
        return {"provider_data": provider_data}

    async def _submit_vmss_delete_if_emptying_async(
        self,
        *,
        resource_group: str,
        vmss_name: str,
    ) -> _VmssCleanupSubmissionState:
        """Async best-effort VMSS delete submission when this return should empty the scale set."""
        try:
            compute = await self.azure_client.get_async_compute_client()
            await compute.virtual_machine_scale_sets.begin_delete(
                resource_group_name=resource_group,
                vm_scale_set_name=vmss_name,
            )
            return self._vmss_delete_submission_result(vmss_name=vmss_name)
        except Exception as exc:
            return self._vmss_delete_submission_result(vmss_name=vmss_name, exc=exc)

    def _should_delete_vmss_when_empty(
        self,
        *,
        orchestration_mode: AzureVMSSOrchestrationMode,
        machine_ids: list[str],
        current_members: list[AzureHandlerStatusResult],
        resolved_instance_ids: list[str],
        resolved_vm_names: list[str] | None = None,
    ) -> bool:
        """Return whether deleting these exact members would leave the VMSS empty."""
        if not machine_ids:
            return False
        if not current_members:
            return False

        current_member_ids = {
            str(instance_id)
            for instance_id in (member.get("instance_id") for member in current_members)
            if instance_id not in (None, "")
        }
        if len(current_member_ids) != len(current_members):
            return False

        if orchestration_mode == AzureVMSSOrchestrationMode.FLEXIBLE:
            requested_ids = {
                str(machine_id)
                for machine_id in (resolved_vm_names or machine_ids)
                if machine_id not in (None, "")
            }
            return bool(requested_ids) and requested_ids == current_member_ids

        requested_ids = {
            str(instance_id)
            for instance_id in resolved_instance_ids
            if instance_id not in (None, "")
        }
        return bool(requested_ids) and requested_ids == current_member_ids

    @staticmethod
    def _resolve_flexible_vm_names_from_members(
        *,
        machine_ids: list[str],
        current_members: list[AzureHandlerStatusResult],
        logger: LoggingPort,
        vmss_name: str,
    ) -> list[str]:
        """Resolve Flexible VMSS requested IDs to Azure VM names."""
        if not machine_ids:
            return []

        lookup: dict[str, str] = {}
        for member in current_members:
            provider_data = member.get("provider_data") or {}

            vm_name = provider_data.get("vm_name") or member.get("name") or member.get("instance_id")
            if not vm_name:
                continue
            resolved_vm_name = str(vm_name)

            for candidate in (
                member.get("instance_id"),
                member.get("name"),
                provider_data.get("vmss_instance_id"),
                provider_data.get("vm_id"),
                provider_data.get("vm_name"),
            ):
                if candidate not in (None, ""):
                    lookup[str(candidate)] = resolved_vm_name

        unresolved_ids = [
            str(machine_id)
            for machine_id in machine_ids
            if str(machine_id) not in lookup
        ]
        if unresolved_ids:
            raise TerminationError(
                f"Could not resolve {len(unresolved_ids)} requested Flexible VMSS member ID(s)",
                resource_ids=unresolved_ids,
                details={
                    "vmss_name": vmss_name,
                    "unresolved_ids": unresolved_ids,
                    "available_member_ids": sorted(lookup),
                },
            )

        resolved = [lookup[str(machine_id)] for machine_id in machine_ids]
        if resolved != [str(machine_id) for machine_id in machine_ids]:
            logger.debug(
                "Resolved Flexible VMSS machine IDs for '%s': %s -> %s",
                vmss_name,
                machine_ids,
                resolved,
            )
        return resolved

    @staticmethod
    def _resolve_vmss_instance_ids_from_members(
        *,
        machine_ids: list[str],
        current_members: list[AzureHandlerStatusResult],
        logger: LoggingPort,
        vmss_name: str,
    ) -> list[str]:
        """Resolve mixed IDs (vm_id/vm_name/instance_id) using already-fetched VMSS members."""
        if not machine_ids:
            return []

        lookup: dict[str, str] = {}
        for vm in current_members:
            vmss_instance_id = str(vm.get("instance_id", "") or "")
            if not vmss_instance_id:
                continue
            lookup[vmss_instance_id] = vmss_instance_id

            provider_data = vm.get("provider_data") or {}
            vm_id = provider_data.get("vm_id")
            if vm_id:
                lookup[str(vm_id)] = vmss_instance_id

            vm_name = provider_data.get("vm_name")
            if vm_name:
                lookup[str(vm_name)] = vmss_instance_id

        unresolved_ids = [
            str(machine_id)
            for machine_id in machine_ids
            if str(machine_id) not in lookup
        ]
        if unresolved_ids:
            raise TerminationError(
                f"Could not resolve {len(unresolved_ids)} requested VMSS member ID(s)",
                resource_ids=unresolved_ids,
                details={
                    "vmss_name": vmss_name,
                    "unresolved_ids": unresolved_ids,
                    "available_member_ids": sorted(lookup),
                },
            )

        resolved = [lookup[str(machine_id)] for machine_id in machine_ids]
        if resolved != [str(mid) for mid in machine_ids]:
            logger.debug(
                "Resolved VMSS machine IDs for '%s': %s -> %s",
                vmss_name,
                machine_ids,
                resolved,
            )
        return resolved

    async def _resolve_vmss_instance_ids_async(
        self,
        resource_group: str,
        vmss_name: str,
        machine_ids: list[str],
        current_members: Optional[list[AzureHandlerStatusResult]] = None,
    ) -> list[str]:
        """Async resolve mixed IDs (vm_id/vm_name/instance_id) to VMSS instance IDs."""
        if not machine_ids:
            return []
        if current_members is None:
            current_members = await self._list_vmss_instances_async(
                resource_group=resource_group,
                vmss_name=vmss_name,
                include_instance_view=False,
                orchestration_mode=AzureVMSSOrchestrationMode.UNIFORM,
            )
        return self._resolve_vmss_instance_ids_from_members(
            machine_ids=machine_ids,
            current_members=current_members,
            logger=self._logger,
            vmss_name=vmss_name,
        )

    async def _list_vmss_instances_async(
        self,
        resource_group: str,
        vmss_name: str,
        include_instance_view: bool = False,
        orchestration_mode: Optional[AzureVMSSOrchestrationMode] = None,
    ) -> list[AzureHandlerStatusResult]:
        """Async list of normalised instance dicts for a VMSS."""
        compute = await self.azure_client.get_async_compute_client()
        if orchestration_mode is None:
            orchestration_mode = await self._get_vmss_orchestration_mode_async(
                resource_group, vmss_name
            )

        if orchestration_mode == AzureVMSSOrchestrationMode.FLEXIBLE:
            return await self._list_flexible_vmss_instances_async(
                resource_group=resource_group,
                vmss_name=vmss_name,
                include_instance_view=include_instance_view,
            )

        try:
            pager = compute.virtual_machine_scale_set_vms.list(
                resource_group_name=resource_group,
                virtual_machine_scale_set_name=vmss_name,
                expand=self._vmss_expand_arg(include_instance_view),
            )
            vms = [vm async for vm in pager]
        except Exception as exc:
            raise VMSSNotFoundError(
                f"Could not list VMs in VMSS '{vmss_name}': {exc}",
                vmss_name=vmss_name,
            ) from exc

        return [
            # cast: SDK 38 exposes hardware_profile/instance_view/vm_id via the
            # __flattened_items __getattr__ shim, invisible to static checkers
            # though documented as the SDK's public surface.
            await self._normalise_vm_async(
                cast(AzureVmRuntimeStatusProtocol, vm), vmss_name, resource_group
            )
            for vm in vms
        ]

    async def get_vmss_resource_errors_async(
        self,
        resource_group: str,
        vmss_name: str,
    ) -> list[ProviderErrorEntry]:
        """Return VMSS-level provisioning errors via the async Azure SDK."""
        compute = await self.azure_client.get_async_compute_client()
        try:
            vmss = await compute.virtual_machine_scale_sets.get(
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
        # getattr: Azure may surface instance-view statuses dynamically here.
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

    async def _get_vmss_orchestration_mode_async(
        self,
        resource_group: str,
        vmss_name: str,
    ) -> AzureVMSSOrchestrationMode:
        compute = await self.azure_client.get_async_compute_client()
        vmss = await compute.virtual_machine_scale_sets.get(
            resource_group_name=resource_group,
            vm_scale_set_name=vmss_name,
        )
        return self._coerce_vmss_orchestration_mode(vmss.orchestration_mode)

    async def _list_flexible_vmss_instances_async(
        self,
        resource_group: str,
        vmss_name: str,
        include_instance_view: bool = False,
    ) -> list[AzureHandlerStatusResult]:
        compute = await self.azure_client.get_async_compute_client()
        try:
            pager = compute.virtual_machines.list(
                **self._flexible_vmss_list_kwargs(
                    resource_group=resource_group,
                    vmss_name=vmss_name,
                    include_instance_view=include_instance_view,
                )
            )
            vms = [vm async for vm in pager]
        except Exception as exc:
            raise VMSSNotFoundError(
                f"Could not list flexible VMs for VMSS '{vmss_name}': {exc}",
                vmss_name=vmss_name,
            ) from exc

        return [
            # cast: SDK 38 exposes hardware_profile/instance_view/vm_id via the
            # __flattened_items __getattr__ shim, invisible to static checkers
            # though documented as the SDK's public surface.
            await self._normalise_vm_async(
                cast(AzureVmRuntimeStatusProtocol, vm), vmss_name, resource_group
            )
            for vm in vms
        ]

    async def _normalise_vm_async(
        self, vm: AzureVmRuntimeStatusProtocol, vmss_name: str, resource_group: str
    ) -> AzureHandlerStatusResult:
        """Async variant of ``_normalise_vm`` with async network enrichment."""
        vm_identity = _read_vm_identity(vm)
        network_identity = await resolve_network_identity_or_empty_async(
            logger=self._logger,
            target_label=f"VMSS member '{vm_identity.instance_id}' in '{vmss_name}'",
            resolver=lambda: self.azure_client.resolve_network_identity_from_vm_async(vm),
        )
        return self._build_normalized_vm_status(
            vm=vm,
            vm_identity=vm_identity,
            vmss_name=vmss_name,
            resource_group=resource_group,
            network_identity=network_identity,
        )

    @staticmethod
    def _vmss_expand_arg(include_instance_view: bool) -> str | None:
        """Return the SDK expand value used for instance-view enrichment."""
        return "instanceView" if include_instance_view else None

    def _flexible_vmss_list_kwargs(
        self,
        *,
        resource_group: str,
        vmss_name: str,
        include_instance_view: bool,
    ) -> dict[str, Any]:
        """Build the VM list filter Azure expects for Flexible VMSS membership."""
        vmss_resource_id = (
            f"/subscriptions/{self.azure_client.subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Compute/virtualMachineScaleSets/{vmss_name}"
        )
        list_kwargs: dict[str, Any] = {
            "resource_group_name": resource_group,
            "filter": f"'virtualMachineScaleSet/id' eq '{vmss_resource_id}'",
        }
        expand = self._vmss_expand_arg(include_instance_view)
        if expand is not None:
            list_kwargs["expand"] = expand
        return list_kwargs

    def _build_normalized_vm_status(
        self,
        *,
        vm: AzureVmRuntimeStatusProtocol,
        vm_identity: _AzureVmIdentity,
        vmss_name: str,
        resource_group: str,
        network_identity: AzureNetworkIdentity,
    ) -> AzureHandlerStatusResult:
        """Build the normalized VMSS member status once network identity is resolved."""
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

        hw = vm.hardware_profile
        instance_type = hw.vm_size if hw else None
        if not instance_type:
            instance_type = "unknown"

        location = vm.location
        zones = vm.zones
        availability_zone = zones[0] if zones else None

        launch_time = None
        if vm_statuses is not None:
            for status_entry in vm_statuses:
                timestamp = status_entry.time
                if timestamp is not None:
                    launch_time = str(timestamp)
                    break

        vnet_id = network_identity["vnet_id"]
        provider_data: AzureStatusProviderData = {
            "resource_id": vmss_name,
            "cloud_host_id": vm_identity.vm_id or vm_identity.instance_id,
            "vmss_name": vmss_name,
            "resource_group": resource_group,
            "vmss_instance_id": vm_identity.instance_id,
            "vm_id": vm_identity.vm_id,
            "availability_zone": availability_zone,
            "nic_id": network_identity["nic_id"],
            "nic_name": network_identity["nic_name"],
            "vnet_id": vnet_id,
            "fleet_errors": [dict(error) for error in fleet_errors],
        }
        if vm_identity.vm_name:
            provider_data["vm_name"] = vm_identity.vm_name
        if location:
            provider_data["location"] = str(location)
        return {
            "instance_id": vm_identity.instance_id,
            "name": vm_identity.vm_name or vm_identity.instance_id,
            "status": status,
            "private_ip": network_identity["private_ip"],
            "public_ip": network_identity["public_ip"],
            "launch_time": launch_time,
            "instance_type": instance_type,
            "subnet_id": network_identity["subnet_id"],
            "vpc_id": vnet_id,
            "tags": vm.tags or {},
            "price_type": None,
            "provider_type": "azure",
            "provider_data": provider_data,
        }

    @staticmethod
    def _coerce_vmss_orchestration_mode(
        raw_mode: Optional["SdkOrchestrationMode"],
    ) -> AzureVMSSOrchestrationMode:
        """Coerce Azure's orchestration-mode field into the provider enum.

        The SDK returns ``Optional[OrchestrationMode]`` (a plain ``Enum``; its
        ``str()`` is the qualified ``"OrchestrationMode.FLEXIBLE"`` form rather
        than the wire value, so we read ``.value`` directly).
        """
        if raw_mode is None:
            return AzureVMSSOrchestrationMode.FLEXIBLE
        return AzureVMSSOrchestrationMode(raw_mode.value)

    def _vmss_delete_submission_result(
        self,
        *,
        vmss_name: str,
        exc: Exception | None = None,
    ) -> _VmssCleanupSubmissionState:
        """Build the normalized delete-submission state for follow-up cleanup."""
        if exc is None:
            self._logger.info(
                "Submitted immediate delete for VMSS '%s' because return should empty it",
                vmss_name,
            )
            return {
                "delete_submitted": True,
                "delete_retry_pending": False,
                "last_delete_error": None,
            }

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
