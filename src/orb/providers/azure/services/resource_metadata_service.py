"""Azure resource metadata enrichment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from orb.domain.base.ports import LoggingPort


@dataclass
class VmssCapacitySnapshot:
    """Normalized VMSS capacity details for one scale set."""

    target_capacity_units: int
    fulfilled_capacity_units: int
    provisioned_instance_count: int
    state: Optional[str]

    def as_metadata(self) -> dict[str, Any]:
        return {
            "target_capacity_units": self.target_capacity_units,
            "fulfilled_capacity_units": self.fulfilled_capacity_units,
            "provisioned_instance_count": self.provisioned_instance_count,
            "state": self.state,
        }


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

    def augment_vmss_capacity_metadata(
        self,
        metadata: dict[str, Any],
        resource_ids: list[str],
        *,
        resource_manager: Any,
        resource_group: Optional[str] = None,
    ) -> None:
        if not resource_ids or resource_manager is None:
            return

        resolved_resource_group = resource_group or self._default_resource_group
        if not resolved_resource_group:
            return

        per_resource_capacity = self._collect_vmss_capacity(
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

    def _collect_vmss_capacity(
        self,
        *,
        resource_group: str,
        resource_ids: list[str],
        resource_manager: Any,
    ) -> dict[str, VmssCapacitySnapshot]:
        per_resource_capacity: dict[str, VmssCapacitySnapshot] = {}
        for vmss_name in self._dedupe_resource_ids(resource_ids):
            snapshot = self._get_vmss_capacity_snapshot(
                resource_group=resource_group,
                vmss_name=vmss_name,
                resource_manager=resource_manager,
            )
            if snapshot is not None:
                per_resource_capacity[vmss_name] = snapshot
        return per_resource_capacity

    def _get_vmss_capacity_snapshot(
        self,
        *,
        resource_group: str,
        vmss_name: str,
        resource_manager: Any,
    ) -> Optional[VmssCapacitySnapshot]:
        try:
            capacity_info = resource_manager.get_vmss_capacity(resource_group, vmss_name)
        except Exception as exc:
            self._logger.warning("Could not fetch VMSS capacity for %s: %s", vmss_name, exc)
            return None

        provisioned_instance_count = int(capacity_info.get("provisioned_instance_count", 0) or 0)
        target_capacity = int(capacity_info.get("capacity", 0) or 0)
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

    def augment_single_vm_deployment_metadata(
        self,
        metadata: dict[str, Any],
        request_metadata: dict[str, Any],
        *,
        resource_group: Optional[str],
        deployment_service: Any,
    ) -> None:
        deployment_name = request_metadata.get("deployment_name")
        if deployment_name in (None, "") or not resource_group or deployment_service is None:
            return

        try:
            deployment_status = deployment_service.get_deployment_status(
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
                    "error_message": error_message
                    or f"ARM deployment '{deployment_name}' failed",
                    "resource_group": str(resource_group),
                    "instance_id": str(deployment_name),
                }
            ]

    @staticmethod
    def augment_shortfall_metadata(metadata: dict[str, Any]) -> None:
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
