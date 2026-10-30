"""GCP mutation result orchestration helpers."""

from __future__ import annotations

from orb.providers.base.strategy import ProviderResult
from orb.providers.gcp.types import GCPMutationOutcome


class GCPMutationService:
    """Own GCP mutation outcome shaping at the strategy/service boundary."""

    @staticmethod
    def build_dry_run_result(
        *,
        operation_name: str,
        attempted_ids: list[str],
        metadata: dict[str, object] | None = None,
        warning: str | None = None,
    ) -> ProviderResult:
        """Return a synthetic mutation result without touching live GCP APIs."""
        response_data: dict[str, object] = {
            "success": True,
            "successful_count": len(attempted_ids),
            "successful_ids": list(attempted_ids),
            "results": {target_id: True for target_id in attempted_ids},
            "failed_operations": [],
        }
        if warning:
            response_data["warning"] = warning

        result_metadata: dict[str, object] = {
            "operation": operation_name,
            "method": "dry_run",
            "provider_data": {
                "dry_run": True,
                "attempted_ids": list(attempted_ids),
                "successful_ids": list(attempted_ids),
                "operations": [],
                "failed_operations": [],
                **({"warning": warning} if warning else {}),
            },
            "partial_failure": False,
        }
        if metadata:
            result_metadata.update(metadata)
        return ProviderResult.success_result(response_data, result_metadata)

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
            attempted_ids = successful_ids + [failure.target_id for failure in failed_operations]

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

        failed_ids = [failure.target_id for failure in failed_operations]
        result_metadata: dict[str, object] = {
            "operation": operation_name,
            "provider_data": {
                "attempted_ids": attempted_ids,
                "successful_ids": successful_ids,
                "successful_instance_ids": successful_ids,
                "failed_instance_ids": failed_ids,
                "operations": outcome.operations,
                "failed_operations": [failure.__dict__ for failure in failed_operations],
                **({"warning": warning} if warning else {}),
            },
            "partial_failure": bool(failed_operations),
        }
        if metadata:
            result_metadata.update(metadata)
        # Only terminate is reported as failed on partial failure: callers act on
        # result.success to terminalise machines, so a failed delete must not
        # read as success. Start/stop keep their partial-success contract.
        if failed_operations and operation_name == "terminate_instances":
            return ProviderResult(
                success=False,
                data=response_data,
                error_message=(
                    f"{len(failed_ids)} of {len(attempted_ids)} GCP instance terminations failed"
                ),
                error_code="GCP_TERMINATE_FAILED",
                metadata=result_metadata,
            )
        return ProviderResult.success_result(response_data, result_metadata)
