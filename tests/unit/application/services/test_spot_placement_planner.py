from application.services.spot_placement_planner import (
    PlacementCandidate,
    PlacementScore,
    SpotPlacementPlanner,
)
from domain.base.value_objects import PlacementSplitStrategy


def _score(candidate_id: str, normalized_score: float, raw_score):
    return PlacementScore(
        candidate=PlacementCandidate(candidate_id=candidate_id, instance_type=candidate_id),
        raw_score=raw_score,
        normalized_score=normalized_score,
    )


def test_greedy_plan_allocates_all_to_top_candidate():
    planner = SpotPlacementPlanner()

    plan = planner.create_plan(
        requested_count=10,
        scores=[
            _score("small", 0.4, 4),
            _score("large", 0.9, 9),
            _score("medium", 0.7, 7),
        ],
        split_strategy=PlacementSplitStrategy.GREEDY,
        primary_share_percent=80,
    )

    assert [(entry.score.candidate.candidate_id, entry.planned_count) for entry in plan] == [
        ("large", 10),
        ("medium", 0),
        ("small", 0),
    ]


def test_hybrid_plan_reserves_primary_share_and_distributes_remainder():
    planner = SpotPlacementPlanner()

    plan = planner.create_plan(
        requested_count=10,
        scores=[
            _score("c1", 1.0, "High"),
            _score("c2", 0.6, "Medium"),
            _score("c3", 0.2, "Low"),
        ],
        split_strategy=PlacementSplitStrategy.HYBRID,
        primary_share_percent=70,
    )

    assert [(entry.score.candidate.candidate_id, entry.planned_count) for entry in plan] == [
        ("c1", 7),
        ("c2", 2),
        ("c3", 1),
    ]


def test_zero_score_candidates_are_filtered_out():
    planner = SpotPlacementPlanner()

    plan = planner.create_plan(
        requested_count=5,
        scores=[
            _score("c1", 0.0, 0),
            _score("c2", 0.9, 9),
        ],
        split_strategy=PlacementSplitStrategy.HYBRID,
        primary_share_percent=80,
    )

    assert len(plan) == 1
    assert plan[0].score.candidate.candidate_id == "c2"
    assert plan[0].planned_count == 5
