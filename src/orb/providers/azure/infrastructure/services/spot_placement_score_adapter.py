"""Azure spot placement score adapter."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx

from orb.application.services.spot_placement_planner import (
    PlacementCandidate,
    PlacementScore,
    SpotPlacementScoreAdapter,
)
from orb.domain.base.ports import LoggingPort
from orb.providers.azure.domain.template.value_objects import AzureLocationName
from orb.providers.azure.infrastructure.azure_client import AzureClient


class AzureSpotPlacementTemplate(Protocol):
    """Structural template interface required for Azure spot placement scoring."""

    vm_size: str
    location: AzureLocationName
    placement_regions: list[str]
    placement_zones: list[str]
    zones: list[str]
    candidate_vm_sizes: list[str]


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

    def score_candidates(
        self, requested_count: int, template: AzureSpotPlacementTemplate
    ) -> list[PlacementScore]:
        """Fetch spot placement scores from sync code only."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.score_candidates_async(
                    requested_count=requested_count,
                    template=template,
                )
            )
        raise TypeError(
            "score_candidates cannot run inside an active event loop; "
            "use score_candidates_async instead"
        )

    async def score_candidates_async(
        self, requested_count: int, template: AzureSpotPlacementTemplate
    ) -> list[PlacementScore]:
        """Fetch and return spot placement scores using async HTTP transport."""
        vm_sizes, regions, zones, candidates = self._candidate_inputs(template)

        if not candidates:
            return []

        raw_scores = await self._fetch_scores_async(
            requested_count=requested_count,
            regions=regions,
            vm_sizes=vm_sizes,
            zones=zones,
        )

        return self._build_scores(candidates=candidates, raw_scores=raw_scores)

    def _candidate_inputs(
        self,
        template: AzureSpotPlacementTemplate,
    ) -> tuple[list[str], list[str], list[str], list[PlacementCandidate]]:
        """Build spot-placement API inputs and matching planner candidates."""
        vm_sizes = template.candidate_vm_sizes
        regions = template.placement_regions or [
            template.location.value or self._base_location
        ]
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
        return vm_sizes, regions, zones, candidates

    async def _fetch_scores_async(
        self,
        requested_count: int,
        regions: list[str],
        vm_sizes: list[str],
        zones: list[str],
    ) -> dict[tuple[str | None, str | None, str], dict[str, Any]]:
        if not self._subscription_id:
            self._logger.warning(
                "Azure subscription_id not available; skipping spot placement scoring"
            )
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
            credential = await self._azure_client.get_async_credential()
            token = await credential.get_token("https://management.azure.com/.default")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token.token}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                response_payload = response.json()
        except Exception as exc:
            self._logger.warning(
                "Azure spot placement score lookup failed: %s", exc, exc_info=True
            )
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

    def _build_scores(
        self,
        *,
        candidates: list[PlacementCandidate],
        raw_scores: dict[tuple[str | None, str | None, str], dict[str, Any]],
    ) -> list[PlacementScore]:
        scores: list[PlacementScore] = []
        for candidate in candidates:
            lookup_key = (candidate.region, candidate.zone, candidate.instance_type)
            entry = raw_scores.get(lookup_key, {})
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

    @classmethod
    def _normalize_score(cls, raw_score: str) -> float:
        return cls._SCORE_MAP.get(str(raw_score).strip().lower(), 0.0)
