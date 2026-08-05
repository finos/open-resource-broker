"""Behavioral contracts for Azure spot placement planning."""

import pytest

from orb.providers.azure.services.spot_placement_planner import (
    PlacementCandidate,
    PlacementPlanEntry,
    PlacementScore,
    PlacementSplitStrategy,
    SpotPlacementPlanner,
)


def _score(
    candidate_id: str,
    normalized_score: float,
    raw_score: object = "high",
) -> PlacementScore:
    return PlacementScore(
        candidate=PlacementCandidate(
            candidate_id=candidate_id,
            instance_type="Standard_D4s_v5",
        ),
        raw_score=raw_score,
        normalized_score=normalized_score,
    )


def _allocations(plan: list[PlacementPlanEntry]) -> list[tuple[str, int]]:
    return [(entry.score.candidate.candidate_id, entry.planned_count) for entry in plan]


@pytest.mark.parametrize("requested_count", [0, -1])
def test_non_positive_requests_have_no_placement_work(requested_count: int) -> None:
    plan = SpotPlacementPlanner().create_plan(
        requested_count=requested_count,
        scores=[_score("candidate-a", 100)],
        split_strategy=PlacementSplitStrategy.GREEDY,
        primary_share_percent=100,
    )

    assert plan == []


def test_candidates_without_positive_capacity_signal_are_not_planned() -> None:
    plan = SpotPlacementPlanner().create_plan(
        requested_count=4,
        scores=[_score("zero", 0), _score("negative", -1)],
        split_strategy=PlacementSplitStrategy.GREEDY,
        primary_share_percent=100,
    )

    assert plan == []


def test_greedy_plan_orders_candidates_by_normalized_then_provider_score() -> None:
    plan = SpotPlacementPlanner().create_plan(
        requested_count=4,
        scores=[
            _score("medium", 80, "medium"),
            _score("numeric", 80, 4),
            _score("high", 80, "HIGH"),
            _score("unknown", 80, object()),
            _score("best-normalized", 90, "low"),
            _score("excluded", 0, "high"),
        ],
        split_strategy=PlacementSplitStrategy.GREEDY,
        primary_share_percent=100,
    )

    assert _allocations(plan) == [
        ("best-normalized", 4),
        ("numeric", 0),
        ("high", 0),
        ("medium", 0),
        ("unknown", 0),
    ]


def test_hybrid_plan_conserves_capacity_and_uses_largest_remainder() -> None:
    plan = SpotPlacementPlanner().create_plan(
        requested_count=10,
        scores=[
            _score("primary", 10),
            _score("secondary", 5),
            _score("tertiary", 3),
            _score("quaternary", 2),
        ],
        split_strategy=PlacementSplitStrategy.HYBRID,
        primary_share_percent=51,
    )

    assert _allocations(plan) == [
        ("primary", 6),
        ("secondary", 2),
        ("tertiary", 1),
        ("quaternary", 1),
    ]
    assert sum(entry.planned_count for entry in plan) == 10


def test_hybrid_plan_with_one_candidate_allocates_everything_to_it() -> None:
    plan = SpotPlacementPlanner().create_plan(
        requested_count=3,
        scores=[_score("only", 50)],
        split_strategy=PlacementSplitStrategy.HYBRID,
        primary_share_percent=25,
    )

    assert _allocations(plan) == [("only", 3)]


def test_hybrid_plan_with_full_primary_share_keeps_ranked_fallbacks() -> None:
    plan = SpotPlacementPlanner().create_plan(
        requested_count=3,
        scores=[_score("primary", 100), _score("fallback", 90)],
        split_strategy=PlacementSplitStrategy.HYBRID,
        primary_share_percent=100,
    )

    assert _allocations(plan) == [("primary", 3), ("fallback", 0)]
