"""Provider-agnostic execution helpers for spot placement plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from orb.application.services.spot_placement_planner import PlacementPlanEntry
from orb.domain.base.exceptions import DomainException


@dataclass(frozen=True)
class SpotPlacementExecutionSummary:
    """Aggregated execution outcome for a placement plan."""

    resource_ids: list[str]
    instances: list[dict[str, Any]]
    child_results: list[dict[str, Any]]
    failed_subplans: list[dict[str, Any]]
    unfulfilled_count: int
    terminated_early: bool = False
    terminal_error_message: str | None = None

    @property
    def provider_data(self) -> dict[str, Any]:
        return {
            "child_results": self.child_results,
            "failed_subplans": self.failed_subplans,
            "unfulfilled_count": self.unfulfilled_count,
            "terminated_early": self.terminated_early,
            "terminal_error_message": self.terminal_error_message,
        }


def serialize_placement_plan(plan: list[PlacementPlanEntry]) -> list[dict[str, Any]]:
    """Convert plan entries into JSON-friendly metadata."""
    return [
        {
            "candidate_id": entry.score.candidate.candidate_id,
            "instance_type": entry.score.candidate.instance_type,
            "region": entry.score.candidate.region,
            "zone": entry.score.candidate.zone,
            "raw_score": entry.score.raw_score,
            "normalized_score": entry.score.normalized_score,
            "approximate": entry.score.approximate,
            "planned_count": entry.planned_count,
            "metadata": entry.score.metadata,
        }
        for entry in plan
    ]


def create_acquire_request(
    template_id: str,
    count: int,
    provider_type: str,
    provider_name: str | None,
    provider_api: str,
    request_metadata: dict[str, Any],
    request_id: str | None = None,
    parent_request_id: str | None = None,
    plan_entry_index: int | None = None,
) -> Any:
    """Create a standard acquire request for a child placement launch."""
    from orb.domain.request.aggregate import Request
    from orb.domain.request.request_types import RequestType

    child_metadata = dict(request_metadata)
    if parent_request_id:
        child_metadata["parent_request_id"] = parent_request_id
    if plan_entry_index is not None:
        child_metadata["spot_placement_plan_entry_index"] = plan_entry_index

    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id=template_id,
        machine_count=count,
        provider_type=provider_type,
        provider_name=provider_name,
        metadata=child_metadata,
        request_id=request_id,
    )
    request.provider_api = provider_api
    return request


def build_planned_execution_metadata(
    plan: list[PlacementPlanEntry],
    summary: SpotPlacementExecutionSummary,
) -> dict[str, Any]:
    """Build common metadata payload for planned execution results."""
    return {
        "placement_plan": serialize_placement_plan(plan),
        **summary.provider_data,
    }


class SpotPlacementExecutionService:
    """Execute a placement plan via provider callbacks."""

    def execute_plan(
        self,
        plan: list[PlacementPlanEntry],
        total_count: int,
        build_child_template: Callable[[PlacementPlanEntry], Any],
        build_child_request: Callable[[int, int], Any],
        launch_child: Callable[[Any, Any], Any],
        is_capacity_like_failure: Callable[[dict[str, Any]], bool],
    ) -> SpotPlacementExecutionSummary:
        resource_ids: list[str] = []
        instances: list[dict[str, Any]] = []
        child_results: list[dict[str, Any]] = []
        failed_subplans: list[dict[str, Any]] = []
        carryover = 0
        fulfilled = 0
        terminated_early = False
        terminal_error_message: str | None = None

        for idx, plan_entry in enumerate(plan):
            requested_for_entry = plan_entry.planned_count + carryover
            if requested_for_entry <= 0:
                continue

            child_template = build_child_template(plan_entry)
            child_request = build_child_request(requested_for_entry, idx)
            try:
                raw_result = launch_child(child_request, child_template)
            except DomainException as exc:
                raw_result = {
                    "success": False,
                    "resource_ids": [],
                    "instances": [],
                    "error_message": self._format_launch_error(exc),
                    "provider_data": {
                        "error_codes": [exc.error_code] if getattr(exc, "error_code", None) else [],
                    },
                }
            child_result = self._normalize_child_result(
                plan_entry=plan_entry,
                requested_count=requested_for_entry,
                raw_result=raw_result,
            )
            child_results.append(child_result)

            if child_result["success"]:
                resource_ids.extend(child_result["resource_ids"])
                instances.extend(child_result["instances"])
                fulfilled += requested_for_entry
                carryover = 0
                continue

            failed_subplans.append(child_result)
            if is_capacity_like_failure(child_result):
                carryover = requested_for_entry
                continue

            if resource_ids or instances:
                terminated_early = True
                terminal_error_message = child_result["error_message"]
                break

            return SpotPlacementExecutionSummary(
                resource_ids=[],
                instances=[],
                child_results=child_results,
                failed_subplans=failed_subplans,
                unfulfilled_count=total_count,
                terminated_early=True,
                terminal_error_message=child_result["error_message"],
            )

        return SpotPlacementExecutionSummary(
            resource_ids=resource_ids,
            instances=instances,
            child_results=child_results,
            failed_subplans=failed_subplans,
            unfulfilled_count=max(total_count - fulfilled, carryover),
            terminated_early=terminated_early,
            terminal_error_message=terminal_error_message,
        )

    @staticmethod
    def _format_launch_error(exc: Exception) -> str:
        """Format a launch exception while keeping the visible error code prefix."""
        error_code = getattr(exc, "error_code", None)
        message = str(exc)
        if error_code and not message.startswith(f"{error_code}:"):
            return f"{error_code}: {message}"
        return message

    @staticmethod
    def _normalize_child_result(
        plan_entry: PlacementPlanEntry,
        requested_count: int,
        raw_result: Any,
    ) -> dict[str, Any]:
        if isinstance(raw_result, dict):
            success = raw_result.get("success", True)
            error_message = raw_result.get("error_message")
            resource_ids = raw_result.get("resource_ids", [])
            instances = raw_result.get("instances", [])
            provider_data = raw_result.get("provider_data") or {}
        else:
            success = bool(raw_result)
            error_message = None if success else "Provisioning returned no resource id"
            resource_ids = [raw_result] if raw_result else []
            instances = []
            provider_data = {}

        return {
            "candidate_id": plan_entry.score.candidate.candidate_id,
            "requested_count": requested_count,
            "success": success,
            "resource_ids": resource_ids,
            "instances": instances,
            "error_message": error_message,
            "error_codes": SpotPlacementExecutionService._extract_error_codes(
                provider_data=provider_data,
            ),
            "provider_data": provider_data,
        }

    @staticmethod
    def _extract_error_codes(
        provider_data: dict[str, Any],
    ) -> list[str]:
        error_codes: list[str] = []

        direct_error_codes = provider_data.get("error_codes", [])
        if isinstance(direct_error_codes, (list, tuple, set)):
            for error_code in direct_error_codes:
                if error_code:
                    error_codes.append(str(error_code))
        elif direct_error_codes:
            error_codes.append(str(direct_error_codes))

        return list(dict.fromkeys(error_codes))
