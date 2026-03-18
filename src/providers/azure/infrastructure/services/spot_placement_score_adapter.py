"""Azure spot placement score adapter."""

from __future__ import annotations

import json
from typing import Any
from urllib import request as urllib_request

from application.services.spot_placement_planner import (
    PlacementCandidate,
    PlacementScore,
    SpotPlacementScoreAdapter,
)
from domain.base.ports import LoggingPort
from providers.azure.infrastructure.azure_client import AzureClient


class AzureSpotPlacementScoreAdapter(SpotPlacementScoreAdapter):
    """Azure candidate scoring using the Spot Placement Scores REST API."""

    _API_VERSION = "2025-02-01-preview"
    _SCORE_MAP = {
        "low": 0.2,
        "medium": 0.6,
        "high": 1.0,
        "datanotfoundorstale": 0.0,
    }

    def __init__(
        self,
        azure_client: AzureClient,
        logger: LoggingPort,
        subscription_id: str | None,
        base_location: str,
    ) -> None:
        self._azure_client = azure_client
        self._logger = logger
        self._subscription_id = subscription_id
        self._base_location = base_location

    def score_candidates(self, requested_count: int, template: Any) -> list[PlacementScore]:
        vm_sizes = [template.vm_size, *(template.vm_sizes or [])]
        regions = template.placement_regions or [template.location or self._base_location]
        zones = template.placement_zones or template.zones or []

        candidates = [
            PlacementCandidate(
                candidate_id=f"azure:{region}:{zone or 'regional'}:{vm_size}",
                instance_type=vm_size,
                region=region,
                zone=zone,
            )
            for region in regions
            for vm_size in vm_sizes
            for zone in (zones or [None])
        ]

        if not candidates:
            return []

        raw_scores = self._fetch_scores(
            requested_count=requested_count,
            regions=regions,
            vm_sizes=vm_sizes,
            zones=zones,
        )

        scores: list[PlacementScore] = []
        for candidate in candidates:
            entry = raw_scores.get((candidate.region, candidate.zone, candidate.instance_type), {})
            raw_score = entry.get("score", "Low")
            scores.append(
                PlacementScore(
                    candidate=candidate,
                    raw_score=raw_score,
                    normalized_score=self._normalize_score(raw_score),
                    approximate=False,
                    metadata={
                        "is_quota_available": entry.get("isQuotaAvailable"),
                        "raw_entry": entry,
                    },
                )
            )
        return scores

    def _fetch_scores(
        self,
        requested_count: int,
        regions: list[str],
        vm_sizes: list[str],
        zones: list[str],
    ) -> dict[tuple[str | None, str | None, str], dict[str, Any]]:
        if not self._subscription_id:
            self._logger.warning("Azure subscription_id not available; skipping spot placement scoring")
            return {}

        payload = {
            "desiredLocations": regions,
            "desiredSizes": [{"sku": vm_size} for vm_size in vm_sizes],
            "desiredCount": requested_count,
            "availabilityZones": bool(zones),
        }

        url = (
            "https://management.azure.com/subscriptions/"
            f"{self._subscription_id}/providers/Microsoft.Compute/locations/"
            f"{self._base_location}/placementScores/spot/generate"
            f"?api-version={self._API_VERSION}"
        )

        try:
            token = self._azure_client.credential.get_token(
                "https://management.azure.com/.default"
            )
            body = json.dumps(payload).encode("utf-8")
            req = urllib_request.Request(
                url=url,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {token.token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib_request.urlopen(req, timeout=30) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            self._logger.warning("Azure spot placement score lookup failed: %s", exc)
            return {}

        placement_scores = response_payload.get("placementScores", [])
        return {
            (
                entry.get("region"),
                entry.get("availabilityZone"),
                entry.get("sku"),
            ): entry
            for entry in placement_scores
        }

    @classmethod
    def _normalize_score(cls, raw_score: str) -> float:
        return cls._SCORE_MAP.get(str(raw_score).strip().lower(), 0.0)
