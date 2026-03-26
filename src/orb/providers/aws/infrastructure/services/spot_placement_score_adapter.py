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
        for idx, instance_type in enumerate(instance_types):
            candidate = PlacementCandidate(
                candidate_id=f"aws:{self._region}:{instance_type}",
                instance_type=instance_type,
                region=self._region,
            )
            peer_group = self._build_peer_group(instance_types, idx)
            raw_score = self._get_score_for_candidate(
                candidate=candidate,
                peer_group=peer_group,
                requested_count=requested_count,
            )
            scores.append(
                PlacementScore(
                    candidate=candidate,
                    raw_score=raw_score,
                    normalized_score=self._normalize_score(raw_score),
                    approximate=True,
                    metadata={"peer_group": peer_group},
                )
            )

        return scores

    @staticmethod
    def _build_peer_group(instance_types: list[str], candidate_index: int) -> list[str]:
        if len(instance_types) <= 3:
            return list(instance_types)

        ordered = [instance_types[candidate_index]]
        for offset in range(1, len(instance_types)):
            next_index = (candidate_index + offset) % len(instance_types)
            ordered.append(instance_types[next_index])
            if len(ordered) == 3:
                break
        return ordered

    def _get_score_for_candidate(
        self,
        candidate: PlacementCandidate,
        peer_group: list[str],
        requested_count: int,
    ) -> int:
        try:
            response = self._aws_client.ec2_client.get_spot_placement_scores(
                InstanceTypes=peer_group,
                TargetCapacity=requested_count,
                TargetCapacityUnitType="units",
                SingleAvailabilityZone=False,
                RegionNames=[candidate.region or self._region],
            )
        except Exception as exc:
            self._logger.warning(
                "AWS spot placement score lookup failed for %s: %s",
                candidate.instance_type,
                exc,
            )
            return 0

        placement_scores = response.get("SpotPlacementScores", [])
        for score_entry in placement_scores:
            if score_entry.get("Region") == (candidate.region or self._region):
                try:
                    return int(score_entry.get("Score", 0) or 0)
                except (TypeError, ValueError):
                    return 0
        return 0

    @staticmethod
    def _normalize_score(raw_score: int) -> float:
        if raw_score <= 0:
            return 0.0
        return min(max(raw_score / 100.0, 0.0), 1.0)
