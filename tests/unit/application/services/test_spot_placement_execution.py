import pytest

from orb.application.services.spot_placement_execution import SpotPlacementExecutionService
from orb.application.services.spot_placement_planner import (
    PlacementCandidate,
    PlacementPlanEntry,
    PlacementScore,
)
from orb.domain.base.exceptions import DomainException


def _plan_entry(candidate_id: str, planned_count: int = 1) -> PlacementPlanEntry:
    region = "eastus2"
    zone = "1"
    instance_type = "Standard_D4s_v5"
    return PlacementPlanEntry(
        score=PlacementScore(
            candidate=PlacementCandidate(
                candidate_id=candidate_id,
                instance_type=instance_type,
                region=region,
                zone=zone,
            ),
            raw_score="High",
            normalized_score=1.0,
        ),
        planned_count=planned_count,
    )


def test_extract_error_codes_prefers_structured_provider_data():
    error_codes = SpotPlacementExecutionService._extract_error_codes(
        provider_data={"error_codes": ["AllocationFailed"], "fleet_errors": []},
    )

    assert error_codes == ["AllocationFailed"]


def test_extract_error_codes_deduplicates_structured_provider_data():
    error_codes = SpotPlacementExecutionService._extract_error_codes(
        provider_data={"error_codes": ["AllocationFailed", "AllocationFailed", "SkuNotAvailable"]},
    )

    assert error_codes == ["AllocationFailed", "SkuNotAvailable"]


def test_execute_plan_propagates_domain_exception_error_code():
    service = SpotPlacementExecutionService()
    plan = [_plan_entry("azure:eastus2:1:Standard_D4s_v5")]

    summary = service.execute_plan(
        plan=plan,
        total_count=1,
        build_child_template=lambda plan_entry: {"candidate_id": plan_entry.score.candidate.candidate_id},
        build_child_request=lambda requested_count, idx: {"count": requested_count, "index": idx},
        launch_child=lambda child_request, child_template: (_ for _ in ()).throw(
            DomainException("No capacity in selected zone", error_code="AllocationFailed")
        ),
        is_capacity_like_failure=lambda child_result: "AllocationFailed" in child_result["error_codes"],
    )

    assert summary.unfulfilled_count == 1
    assert summary.failed_subplans[0]["error_codes"] == ["AllocationFailed"]
    assert summary.child_results[0]["provider_data"]["error_codes"] == ["AllocationFailed"]


def test_execute_plan_rejects_non_mapping_child_result():
    service = SpotPlacementExecutionService()
    plan = [_plan_entry("azure:eastus2:1:Standard_D4s_v5")]

    with pytest.raises(TypeError, match="child launch must return a mapping result"):
        service.execute_plan(
            plan=plan,
            total_count=1,
            build_child_template=lambda plan_entry: {"candidate_id": plan_entry.score.candidate.candidate_id},
            build_child_request=lambda requested_count, idx: {"count": requested_count, "index": idx},
            launch_child=lambda child_request, child_template: "legacy-resource-id",
            is_capacity_like_failure=lambda child_result: False,
        )
