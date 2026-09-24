"""GCP inventory/read result shaping helpers."""

from __future__ import annotations

from orb.domain.base.provider_fulfilment import ProviderFulfilment
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
        requested_count: int | None,
    ) -> ProviderResult:
        """Convert normalized instance status records into the ORB result schema."""
        fulfilment = GCPInventoryService._build_provider_fulfilment(
            instances=instances,
            requested_count=requested_count,
        )
        return ProviderResult.success_result(
            {"instances": instances},
            {
                "operation": operation_name,
                "provider_fulfilment": fulfilment,
            },
        )

    @staticmethod
    def _build_provider_fulfilment(
        *,
        instances: list[GCPInstanceStatus],
        requested_count: int | None,
    ) -> ProviderFulfilment:
        """Compute the provider fulfilment verdict from normalized GCP statuses."""
        target_units = requested_count if requested_count is not None else len(instances)
        running_count = sum(1 for instance in instances if instance.get("status") == "running")
        failed_count = sum(
            1
            for instance in instances
            if instance.get("status") in {"failed", "stopped", "terminated"}
        )
        pending_count = max(len(instances) - running_count - failed_count, 0)

        if target_units == 0:
            return ProviderFulfilment(
                state="fulfilled",
                message="No instances requested",
                target_units=0,
                fulfilled_units=0,
                running_count=0,
                pending_count=0,
                failed_count=0,
            )

        if running_count >= target_units:
            return ProviderFulfilment(
                state="fulfilled",
                message=f"All {target_units} GCP instances are running",
                target_units=target_units,
                fulfilled_units=running_count,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )

        if failed_count and not running_count and not pending_count:
            return ProviderFulfilment(
                state="failed",
                message=f"GCP provisioning failed for {failed_count}/{target_units} instances",
                target_units=target_units,
                fulfilled_units=running_count,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )

        if failed_count and running_count:
            return ProviderFulfilment(
                state="partial",
                message=f"GCP provisioned {running_count}/{target_units} instances with {failed_count} failed",
                target_units=target_units,
                fulfilled_units=running_count,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )

        return ProviderFulfilment(
            state="in_progress",
            message=f"GCP provisioning in progress: {running_count}/{target_units} instances running",
            target_units=target_units,
            fulfilled_units=running_count,
            running_count=running_count,
            pending_count=pending_count,
            failed_count=failed_count,
        )
