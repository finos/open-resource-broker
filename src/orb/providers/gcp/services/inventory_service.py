"""GCP inventory/read result shaping helpers."""

from __future__ import annotations

from orb.providers.base.strategy import ProviderResult
from orb.providers.gcp.types import GCPInstanceStatus


class GCPInventoryService:
    """Own result shaping for GCP read/status operations."""

    @staticmethod
    def build_dry_run_status_result(
        *,
        operation_name: str,
        instance_ids: list[str],
    ) -> ProviderResult:
        """Return synthetic instance state records for dry-run read operations."""
        return ProviderResult.success_result(
            {
                "instances": [
                    {
                        "instance_id": instance_id,
                        "status": "DRY_RUN",
                        "provider_data": {"dry_run": True},
                    }
                    for instance_id in instance_ids
                ]
            },
            {
                "operation": operation_name,
                "method": "dry_run",
                "provider_data": {"dry_run": True},
            },
        )

    @staticmethod
    def build_dry_run_describe_result(
        *,
        resource_ids: list[str],
        provider_api: str | None,
    ) -> ProviderResult:
        """Return a synthetic describe result for dry-run resource lookups."""
        return ProviderResult.success_result(
            {"instances": []},
            {
                "operation": "describe_resource_instances",
                "resource_ids": resource_ids,
                "provider_api": provider_api,
                "method": "dry_run",
                "provider_data": {"dry_run": True},
            },
        )

    @staticmethod
    def build_status_result(
        *,
        operation_name: str,
        instances: list[GCPInstanceStatus],
    ) -> ProviderResult:
        """Convert normalized instance status records into the ORB result schema."""
        return ProviderResult.success_result(
            {"instances": instances},
            {"operation": operation_name},
        )
