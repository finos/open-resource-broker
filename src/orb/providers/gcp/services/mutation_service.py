"""GCP mutation result orchestration helpers."""

from __future__ import annotations

from orb.providers.base.strategy import ProviderResult
from orb.providers.gcp.types import GCPMutationOutcome


class GCPMutationService:
    """Own GCP mutation outcome shaping at the strategy/service boundary."""

    @staticmethod
    def build_provider_result(
        *,
        operation_name: str,
        outcome: GCPMutationOutcome,
        metadata: dict[str, object] | None = None,
    ) -> ProviderResult:
        """Convert a provider-native mutation outcome into the ORB result schema."""
        successful_ids = list(outcome.successful_ids)
        failed_operations = list(outcome.failed_operations)
        attempted_ids = list(outcome.attempted_ids)
        if not attempted_ids:
            attempted_ids = successful_ids + [
                failure.target_id for failure in failed_operations
            ]

        success_set = set(successful_ids)
        response_data: dict[str, object] = {
            "success": not failed_operations,
            "successful_count": len(successful_ids),
            "successful_ids": successful_ids,
            "results": {target_id: target_id in success_set for target_id in attempted_ids},
            "failed_operations": [failure.__dict__ for failure in failed_operations],
        }
        warning = outcome.warning
        if isinstance(warning, str) and warning:
            response_data["warning"] = warning

        result_metadata: dict[str, object] = {
            "operation": operation_name,
            "provider_data": {
                "attempted_ids": attempted_ids,
                "successful_ids": successful_ids,
                "operations": outcome.operations,
                "failed_operations": [failure.__dict__ for failure in failed_operations],
                **({"warning": warning} if warning else {}),
            },
            "partial_failure": bool(failed_operations),
        }
        if metadata:
            result_metadata.update(metadata)
        return ProviderResult.success_result(response_data, result_metadata)
