"""AWS spot placement score adapter."""

from __future__ import annotations

from typing import Any

from orb.application.services.spot_placement_planner import (
    PlacementCandidate,
    PlacementScore,
    SpotPlacementScoreAdapter,
)
from orb.domain.base.ports import LoggingPort
from orb.providers.aws.infrastructure.aws_client import AWSClient


class AWSSpotPlacementScoreAdapter(SpotPlacementScoreAdapter):
    """Approximate AWS candidate scoring using GetSpotPlacementScores."""

    def __init__(self, aws_client: AWSClient, logger: LoggingPort, region: str) -> None:
        self._aws_client = aws_client
        self._logger = logger
        self._region = region

    def score_candidates(self, requested_count: int, template: Any) -> list[PlacementScore]:
        instance_types = list((template.machine_types or {}).keys())
        if template.instance_type and template.instance_type not in instance_types:
            instance_types.insert(0, template.instance_type)

        if len(instance_types) < 2:
            return []

        scores: list[PlacementScore] = []
        for instance_type in instance_types:
            candidate = PlacementCandidate(
                candidate_id=f"aws:{self._region}:{instance_type}",
                instance_type=instance_type,
                region=self._region,
            )
            score_entry = self._get_score_for_candidate(
                candidate=candidate,
                requested_count=requested_count,
            )
            raw_score = self._score_value(score_entry)
            availability_zone = score_entry.get("AvailabilityZone")
            if availability_zone:
                candidate = PlacementCandidate(
                    candidate_id=f"aws:{self._region}:{availability_zone}:{instance_type}",
                    instance_type=instance_type,
                    region=self._region,
                    zone=str(availability_zone),
                )
            scores.append(
                PlacementScore(
                    candidate=candidate,
                    raw_score=raw_score,
                    normalized_score=self._normalize_score(raw_score),
                    approximate=True,
                    metadata={"score_entry": score_entry},
                )
            )

        return scores

    def _get_score_for_candidate(
        self,
        candidate: PlacementCandidate,
        requested_count: int,
    ) -> dict[str, Any]:
        try:
            response = self._aws_client.ec2_client.get_spot_placement_scores(
                InstanceTypes=[candidate.instance_type],
                TargetCapacity=requested_count,
                TargetCapacityUnitType="units",
                SingleAvailabilityZone=True,
                RegionNames=[candidate.region or self._region],
            )
        except Exception as exc:
            self._logger.warning(
                "AWS spot placement score lookup failed for %s: %s",
                candidate.instance_type,
                exc,
            )
            return {}

        placement_scores = response.get("SpotPlacementScores", [])
        if not isinstance(placement_scores, list):
            return {}
        matching_scores = [
            score_entry
            for score_entry in placement_scores
            if isinstance(score_entry, dict)
            and score_entry.get("Region") == (candidate.region or self._region)
        ]
        if not matching_scores:
            return {}
        return max(matching_scores, key=self._score_value)

    @staticmethod
    def _score_value(score_entry: dict[str, Any]) -> int:
        try:
            return int(score_entry.get("Score", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _normalize_score(raw_score: int) -> float:
        if raw_score <= 0:
            return 0.0
        return min(max(raw_score / 100.0, 0.0), 1.0)
