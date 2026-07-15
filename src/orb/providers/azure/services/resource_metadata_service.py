"""Azure resource metadata enrichment."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional, Protocol, TypedDict

from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import (
    CheckHostsStatusResult,
    FulfilmentState,
    ProviderFulfilment,
)

_RUNNING_STATES = frozenset({"running"})
_PENDING_STATES = frozenset({"pending", "creating", "starting", "updating", "unknown"})
_FAILED_STATES = frozenset({"failed", "terminated", "stopped", "deallocated"})


@dataclass
class VmssCapacitySnapshot:
    """Normalized VMSS capacity details for one scale set."""

    target_capacity_units: int
    fulfilled_capacity_units: int
    provisioned_instance_count: int
    state: Optional[str]

    def as_metadata(self) -> dict[str, Any]:
        """Return the snapshot as a plain dict suitable for metadata output."""
        return {
            "target_capacity_units": self.target_capacity_units,
            "fulfilled_capacity_units": self.fulfilled_capacity_units,
            "provisioned_instance_count": self.provisioned_instance_count,
            "state": self.state,
        }


class VmssCapacityInfo(TypedDict):
    """VMSS capacity payload returned by Azure resource management."""

    vmss_name: str
    resource_group: str
    capacity: int
    vm_size: str | None
    provisioning_state: str | None
    provisioned_instance_count: int


class AzureResourceManagerProtocol(Protocol):
    """Structural subset of AzureResourceManager used for metadata enrichment."""

    async def get_vmss_capacity_async(
        self, resource_group: str, vmss_name: str
    ) -> VmssCapacityInfo:
        """Return VMSS capacity details for one scale set via the async SDK."""
        ...


class AzureDeploymentStatusServiceProtocol(Protocol):
    """Structural subset of AzureDeploymentService used for deployment status enrichment."""

    async def get_deployment_status_async(
        self,
        *,
        resource_group: str,
        deployment_name: str,
    ) -> Optional[dict[str, object]]:
        """Return deployment provisioning/error state for one ARM deployment via the async SDK."""
        ...


class AzureResourceMetadataService:
    """Own Azure-specific metadata enrichment for discovery/status flows."""

    def __init__(self, default_resource_group: Optional[str], logger: LoggingPort) -> None:
        self._default_resource_group = default_resource_group
        self._logger = logger

    @staticmethod
    def _dedupe_resource_ids(resource_ids: list[str]) -> list[str]:
        deduped: list[str] = []
        for resource_id in resource_ids:
            vmss_name = str(resource_id)
            if vmss_name and vmss_name not in deduped:
                deduped.append(vmss_name)
        return deduped

    async def augment_vmss_capacity_metadata_async(
        self,
        metadata: dict[str, Any],
        resource_ids: list[str],
        *,
        resource_manager: AzureResourceManagerProtocol | None,
        resource_group: Optional[str] = None,
    ) -> None:
        """Async enrich metadata with aggregate VMSS capacity fulfilment."""
        if not resource_ids or resource_manager is None:
            return

        resolved_resource_group = resource_group or self._default_resource_group
        if not resolved_resource_group:
            return

        per_resource_capacity = await self._collect_vmss_capacity_async(
            resource_group=resolved_resource_group,
            resource_ids=resource_ids,
            resource_manager=resource_manager,
        )
        if not per_resource_capacity:
            return

        aggregate_snapshot = self._aggregate_vmss_capacity(per_resource_capacity)
        metadata["fleet_capacity_fulfilment"] = aggregate_snapshot.as_metadata()
        if len(per_resource_capacity) > 1:
            metadata["fleet_capacity_fulfilment_by_resource"] = {
                vmss_name: snapshot.as_metadata()
                for vmss_name, snapshot in per_resource_capacity.items()
            }

    async def _collect_vmss_capacity_async(
        self,
        *,
        resource_group: str,
        resource_ids: list[str],
        resource_manager: AzureResourceManagerProtocol,
    ) -> dict[str, VmssCapacitySnapshot]:
        vmss_names = self._dedupe_resource_ids(resource_ids)
        snapshots = await asyncio.gather(
            *[
                self._get_vmss_capacity_snapshot_async(
                    resource_group=resource_group,
                    vmss_name=vmss_name,
                    resource_manager=resource_manager,
                )
                for vmss_name in vmss_names
            ]
        )

        per_resource_capacity: dict[str, VmssCapacitySnapshot] = {}
        for vmss_name, snapshot in zip(vmss_names, snapshots, strict=True):
            if snapshot is not None:
                per_resource_capacity[vmss_name] = snapshot
        return per_resource_capacity

    async def _get_vmss_capacity_snapshot_async(
        self,
        *,
        resource_group: str,
        vmss_name: str,
        resource_manager: AzureResourceManagerProtocol,
    ) -> Optional[VmssCapacitySnapshot]:
        try:
            capacity_info = await resource_manager.get_vmss_capacity_async(
                resource_group,
                vmss_name,
            )
        except Exception as exc:
            self._logger.warning(
                "Could not fetch VMSS capacity for %s: %s",
                vmss_name,
                exc,
                exc_info=True,
            )
            return None

        provisioned_instance_count = capacity_info["provisioned_instance_count"]
        target_capacity = capacity_info["capacity"]
        provisioning_state = capacity_info.get("provisioning_state")
        return VmssCapacitySnapshot(
            target_capacity_units=target_capacity,
            fulfilled_capacity_units=provisioned_instance_count,
            provisioned_instance_count=provisioned_instance_count,
            state=str(provisioning_state) if provisioning_state not in (None, "") else None,
        )

    @staticmethod
    def _aggregate_vmss_capacity(
        per_resource_capacity: dict[str, VmssCapacitySnapshot],
    ) -> VmssCapacitySnapshot:
        states = [snapshot.state for snapshot in per_resource_capacity.values() if snapshot.state]
        aggregate_state = None
        if len(per_resource_capacity) == 1:
            aggregate_state = next(iter(per_resource_capacity.values())).state
        elif states:
            aggregate_state = states[0] if len(set(states)) == 1 else "multiple"

        target_capacity = sum(
            snapshot.target_capacity_units for snapshot in per_resource_capacity.values()
        )
        fulfilled_capacity = sum(
            snapshot.fulfilled_capacity_units for snapshot in per_resource_capacity.values()
        )
        return VmssCapacitySnapshot(
            target_capacity_units=target_capacity,
            fulfilled_capacity_units=fulfilled_capacity,
            provisioned_instance_count=fulfilled_capacity,
            state=aggregate_state,
        )

    async def augment_single_vm_deployment_metadata_async(
        self,
        metadata: dict[str, Any],
        request_metadata: dict[str, Any],
        *,
        resource_group: Optional[str],
        deployment_service: AzureDeploymentStatusServiceProtocol | None,
    ) -> None:
        """Async enrich metadata with ARM deployment status for single-VM resources."""
        deployment_name = request_metadata.get("deployment_name")
        if deployment_name in (None, "") or not resource_group or deployment_service is None:
            return

        try:
            deployment_status = await deployment_service.get_deployment_status_async(
                resource_group=str(resource_group),
                deployment_name=str(deployment_name),
            )
        except Exception as exc:
            self._logger.warning(
                "Could not fetch SingleVM deployment status for %s: %s",
                deployment_name,
                exc,
            )
            return

        if not deployment_status:
            return

        metadata["deployment_name"] = str(deployment_name)
        provisioning_state = deployment_status.get("provisioning_state")
        if provisioning_state not in (None, ""):
            metadata["deployment_provisioning_state"] = provisioning_state

        error_code = deployment_status.get("error_code")
        error_message = deployment_status.get("error_message")
        if str(provisioning_state).lower() == "failed" and error_code in (None, ""):
            error_code = "DeploymentFailed"
        if error_code not in (None, "") or error_message not in (None, ""):
            metadata["fleet_errors"] = [
                {
                    "error_code": error_code or "DeploymentFailed",
                    "error_message": error_message or f"ARM deployment '{deployment_name}' failed",
                    "resource_group": str(resource_group),
                    "instance_id": str(deployment_name),
                }
            ]

    @staticmethod
    def augment_shortfall_metadata(metadata: dict[str, Any]) -> None:
        """Add a capacity_shortfall summary when fulfilled capacity is below the target."""
        capacity = metadata.get("fleet_capacity_fulfilment") or {}
        target = capacity.get("target_capacity_units")
        fulfilled = capacity.get("fulfilled_capacity_units")
        fleet_errors = metadata.get("fleet_errors") or []

        if target is None or fulfilled is None:
            return
        if fulfilled >= target and not fleet_errors:
            return

        missing_capacity = max(int(target) - int(fulfilled), 0)
        likely_causes: list[str] = []
        seen_causes: set[str] = set()

        for error in fleet_errors:
            error_code = str((error or {}).get("error_code") or "")
            cause = error_code or "Unknown"
            if cause not in seen_causes:
                likely_causes.append(cause)
                seen_causes.add(cause)

        metadata["capacity_shortfall"] = {
            "missing_capacity_units": missing_capacity,
            "likely_causes": likely_causes,
            "summary": (
                f"Shortfall {fulfilled}/{target}"
                + (f"; causes={', '.join(likely_causes)}" if likely_causes else "")
            ),
        }

    def attach_provider_fulfilment(
        self,
        metadata: dict[str, Any],
        *,
        instances: Sequence[Mapping[str, Any]],
        target_units: int | None,
    ) -> CheckHostsStatusResult:
        """Attach and return the canonical provider status contract."""
        status_result = CheckHostsStatusResult(
            instances=[dict(instance) for instance in instances],
            fulfilment=_build_provider_fulfilment(
                metadata=metadata,
                instances=instances,
                target_units=target_units,
            ),
        )
        metadata["provider_fulfilment"] = status_result.fulfilment
        return status_result


def _build_provider_fulfilment(
    *,
    metadata: dict[str, Any],
    instances: Sequence[Mapping[str, Any]],
    target_units: int | None,
) -> ProviderFulfilment:
    fleet_errors = metadata.get("fleet_errors") or []
    # ProviderResult.metadata is the core provider result bag typed as dict[str, Any].
    # Azure owns this key, but reading it back from the core metadata boundary
    # requires validating the shape before trusting the capacity fields.
    capacity = metadata.get("fleet_capacity_fulfilment")
    if isinstance(capacity, dict):
        target = capacity.get("target_capacity_units")
        fulfilled = capacity.get("fulfilled_capacity_units")
        if isinstance(target, int) and isinstance(fulfilled, int):
            capacity_state = str(capacity.get("state") or "").lower()
            if fulfilled >= target and target > 0 and not fleet_errors:
                return ProviderFulfilment(
                    state="fulfilled",
                    message=f"Azure capacity fulfilled: {fulfilled}/{target}",
                    target_units=target,
                    fulfilled_units=fulfilled,
                )

            if capacity_state == "failed" or fleet_errors:
                capacity_fulfilment_state: FulfilmentState = (
                    "partial" if fulfilled > 0 else "failed"
                )
                return ProviderFulfilment(
                    state=capacity_fulfilment_state,
                    message=f"Azure capacity shortfall: {fulfilled}/{target}",
                    target_units=target,
                    fulfilled_units=fulfilled,
                )

            return ProviderFulfilment(
                state="in_progress",
                message=f"Azure capacity provisioning: {fulfilled}/{target}",
                target_units=target,
                fulfilled_units=fulfilled,
            )

    # Handler-backed status, SingleVM, CycleCloud, and dry-run results do not
    # have VMSS capacity metadata; derive their verdict from observed statuses.
    target = target_units
    running_count = _count_statuses(instances, _RUNNING_STATES)
    pending_count = _count_statuses(instances, _PENDING_STATES)
    failed_count = _count_statuses(instances, _FAILED_STATES)

    if fleet_errors and running_count == 0:
        return ProviderFulfilment(
            state="failed",
            message="Azure provisioning failed before capacity became available",
            target_units=target,
            fulfilled_units=running_count,
        )

    if target is not None and target > 0 and running_count >= target and failed_count == 0:
        return ProviderFulfilment(
            state="fulfilled",
            message=f"Azure instances running: {running_count}/{target}",
            target_units=target,
            fulfilled_units=running_count,
        )

    if failed_count > 0 and pending_count == 0:
        state: FulfilmentState = "partial" if running_count > 0 else "failed"
        return ProviderFulfilment(
            state=state,
            message=(
                f"Azure instance shortfall: {running_count}/{target}"
                if target is not None
                else f"Azure instance failure: {running_count} running"
            ),
            target_units=target,
            fulfilled_units=running_count,
        )

    return ProviderFulfilment(
        state="in_progress",
        message=(
            f"Azure instances provisioning: {running_count}/{target}"
            if target is not None
            else f"Azure instances provisioning: {running_count} running"
        ),
        target_units=target,
        fulfilled_units=running_count,
    )


def _count_statuses(
    instances: Sequence[Mapping[str, Any]],
    statuses: frozenset[str],
) -> int:
    return sum(1 for instance in instances if str(instance.get("status") or "").lower() in statuses)
