"""Generic spot placement score planning service."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Any, Protocol

from orb.domain.base.value_objects import PlacementSplitStrategy


@dataclass(frozen=True)
class PlacementCandidate:
    """Provider-neutral placement candidate."""

    candidate_id: str
    instance_type: str
    region: str | None = None
    zone: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlacementScore:
    """Placement score for a candidate."""

    candidate: PlacementCandidate
    raw_score: Any
    normalized_score: float
    approximate: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlacementPlanEntry:
    """Concrete count allocation for a placement candidate."""

    score: PlacementScore
    planned_count: int


class SpotPlacementScoreAdapter(Protocol):
    """Provider-specific scoring adapter contract."""

    def score_candidates(
        self,
        requested_count: int,
        template: Any,
    ) -> list[PlacementScore]:
        """Return normalized placement scores for the provider's candidates."""
        ...


class SpotPlacementPlanner:
    """Build a placement plan from normalized candidate scores."""

    def create_plan(
        self,
        requested_count: int,
        scores: list[PlacementScore],
        split_strategy: PlacementSplitStrategy,
        primary_share_percent: int,
    ) -> list[PlacementPlanEntry]:
        """Create an ordered placement plan."""
        if requested_count <= 0:
            return []

        ranked_scores = self._rank_scores(scores)
        if not ranked_scores:
            return []

        if split_strategy == PlacementSplitStrategy.GREEDY or len(ranked_scores) == 1:
            return self._create_greedy_plan(requested_count, ranked_scores)

        return self._create_hybrid_plan(
            requested_count=requested_count,
            scores=ranked_scores,
            primary_share_percent=primary_share_percent,
        )

    def _rank_scores(self, scores: list[PlacementScore]) -> list[PlacementScore]:
        ranked = [score for score in scores if score.normalized_score > 0]
        ranked.sort(
            key=lambda score: (
                score.normalized_score,
                self._raw_score_sort_value(score.raw_score),
                score.candidate.candidate_id,
            ),
            reverse=True,
        )
        return ranked

    @staticmethod
    def _raw_score_sort_value(raw_score: Any) -> float:
        if isinstance(raw_score, (int, float)):
            return float(raw_score)
        if isinstance(raw_score, str):
            mapping = {"low": 1.0, "medium": 2.0, "high": 3.0}
            return mapping.get(raw_score.lower(), 0.0)
        return 0.0

    @staticmethod
    def _create_greedy_plan(
        requested_count: int,
        scores: list[PlacementScore],
    ) -> list[PlacementPlanEntry]:
        return [
            PlacementPlanEntry(
                score=scores[0],
                planned_count=requested_count,
            ),
            *[
                PlacementPlanEntry(score=score, planned_count=0)
                for score in scores[1:]
            ],
        ]

    def _create_hybrid_plan(
        self,
        requested_count: int,
        scores: list[PlacementScore],
        primary_share_percent: int,
    ) -> list[PlacementPlanEntry]:
        top_score = scores[0]
        remainder_scores = scores[1:]

        top_count = ceil(requested_count * primary_share_percent / 100)
        top_count = min(top_count, requested_count)
        remainder_count = requested_count - top_count

        plan_entries = [PlacementPlanEntry(score=top_score, planned_count=top_count)]
        if remainder_count <= 0 or not remainder_scores:
            return plan_entries + [
                PlacementPlanEntry(score=score, planned_count=0) for score in remainder_scores
            ]

        total_weight = sum(score.normalized_score for score in remainder_scores)
        if total_weight <= 0:
            return self._create_greedy_plan(requested_count, scores)

        raw_allocations: list[tuple[PlacementScore, int, float]] = []
        assigned = 0
        for score in remainder_scores:
            exact_count = remainder_count * (score.normalized_score / total_weight)
            allocated = int(exact_count)
            raw_allocations.append((score, allocated, exact_count - allocated))
            assigned += allocated

        leftovers = remainder_count - assigned
        if leftovers > 0:
            raw_allocations.sort(
                key=lambda item: (
                    item[2],
                    item[0].normalized_score,
                    self._raw_score_sort_value(item[0].raw_score),
                    item[0].candidate.candidate_id,
                ),
                reverse=True,
            )
            for idx in range(leftovers):
                score, allocated, fraction = raw_allocations[idx]
                raw_allocations[idx] = (score, allocated + 1, fraction)

            raw_allocations.sort(
                key=lambda item: (
                    item[0].normalized_score,
                    self._raw_score_sort_value(item[0].raw_score),
                    item[0].candidate.candidate_id,
                ),
                reverse=True,
            )

        plan_entries.extend(
            PlacementPlanEntry(score=score, planned_count=allocated)
            for score, allocated, _ in raw_allocations
        )
        return plan_entries
