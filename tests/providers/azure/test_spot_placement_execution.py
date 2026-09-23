"""Contract tests for Azure spot-placement child execution results."""

from typing import Any

import pytest

from orb.providers.azure.services.spot_launch_service import AzureSpotLaunchService
from orb.providers.azure.services.spot_placement_execution import SpotPlacementExecutionService
from orb.providers.azure.services.spot_placement_planner import (
    PlacementCandidate,
    PlacementPlanEntry,
    PlacementScore,
)


def _plan_entry(*, planned_count: int = 2) -> PlacementPlanEntry:
    return PlacementPlanEntry(
        score=PlacementScore(
            candidate=PlacementCandidate(
                candidate_id="azure:eastus2:1:Standard_D4s_v5",
                instance_type="Standard_D4s_v5",
                region="eastus2",
                zone="1",
            ),
            raw_score="High",
            normalized_score=1.0,
        ),
        planned_count=planned_count,
    )


@pytest.mark.asyncio
async def test_missing_success_is_a_terminal_child_contract_failure():
    service = SpotPlacementExecutionService()

    async def launch_child(_request: Any, _template: Any) -> dict[str, Any]:
        return {"resource_ids": ["vmss-never-confirmed"], "instances": []}

    summary = await service.execute_plan_async(
        plan=[_plan_entry()],
        total_count=2,
        build_child_template=lambda _entry: object(),
        build_child_request=lambda _count, _index: object(),
        launch_child=launch_child,
        is_capacity_like_failure=lambda _result: False,
    )

    assert summary.resource_ids == []
    assert summary.unfulfilled_count == 2
    assert summary.terminated_early is True
    assert summary.terminal_error_message == (
        "Child launch result is missing required 'success' field"
    )
    assert summary.child_results[0]["success"] is False
    assert summary.child_results[0]["fulfilled_count"] == 0


@pytest.mark.asyncio
async def test_successful_underfulfillment_records_a_terminal_reason():
    service = SpotPlacementExecutionService()

    async def launch_child(_request: Any, _template: Any) -> dict[str, Any]:
        return {
            "success": True,
            "resource_ids": ["vmss-partial"],
            "instances": [],
            "fulfilled_count": 1,
            "error_message": None,
        }

    summary = await service.execute_plan_async(
        plan=[_plan_entry()],
        total_count=2,
        build_child_template=lambda _entry: object(),
        build_child_request=lambda _count, _index: object(),
        launch_child=launch_child,
        is_capacity_like_failure=lambda _result: False,
    )

    expected_reason = (
        "Child launch for 'azure:eastus2:1:Standard_D4s_v5' fulfilled 1 of 2 requested instances"
    )
    assert summary.resource_ids == ["vmss-partial"]
    assert summary.unfulfilled_count == 1
    assert summary.terminated_early is True
    assert summary.terminal_error_message == expected_reason
    assert summary.failed_subplans[0]["error_message"] == expected_reason


def test_child_fulfillment_does_not_default_missing_success_to_fully_fulfilled():
    result = AzureSpotLaunchService._planned_child_result_with_fulfillment(
        provider_api_key="VMSS",
        requested_count=2,
        raw_result={"resource_ids": ["vmss-never-confirmed"]},
    )

    assert result["fulfilled_count"] == 0
