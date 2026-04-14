"""GCP acquire/provisioning result shaping helpers."""

from __future__ import annotations

from orb.providers.base.strategy import ProviderResult
from orb.providers.gcp.types import GCPCreateOperationContext, GCPCreateOutcome


class GCPProvisioningService:
    """Own GCP create result shaping."""

    @staticmethod
    def create_instances_dry_run_result(
        *,
        context: GCPCreateOperationContext,
    ) -> ProviderResult:
        """Return a synthetic create result without touching live GCP APIs."""
        provider_api = context.template.provider_api.value
        provider_data: dict[str, object] = {
            "dry_run": True,
            "provider_api": provider_api,
        }

        if provider_api == "SingleVM":
            zone = str(context.template.zones[0]) if context.template.zones else ""
            instances = [
                {
                    "instance_id": f"dry-run-{context.template.template_id}-{index + 1}",
                    "status": "DRY_RUN",
                    "provider_data": {
                        "dry_run": True,
                        "zone": zone,
                    },
                }
                for index in range(context.count)
            ]
            provider_data.update(
                {
                    "zone": zone,
                    "requested_count": context.count,
                    "submitted_count": context.count,
                    "operation_status": "dry_run",
                }
            )
            return ProviderResult.success_result(
                {
                    "resource_ids": [instance["instance_id"] for instance in instances],
                    "instances": instances,
                    "provider_api": provider_api,
                    "count": context.count,
                    "template_id": context.template.template_id,
                    "failed_operations": [],
                    "results": {instance["instance_id"]: True for instance in instances},
                },
                {
                    "operation": "create_instances",
                    "handler_used": provider_api,
                    "method": "dry_run",
                    "provider_data": provider_data,
                    "partial_failure": False,
                },
            )

        mig_name = context.template.mig_name or f"dry-run-{context.template.template_id}"
        scope = context.template.mig_scope.value
        provider_data.update(
            {
                "mig_name": mig_name,
                "instance_template_name": (
                    f"dry-run-{context.template.instance_template_name_prefix or 'orb'}"
                    f"-{context.template.template_id}"
                ),
                "target_size": context.count,
                "operation_status": "dry_run",
                "scope": scope,
            }
        )
        if context.template.region:
            provider_data["region"] = str(context.template.region)
        if context.template.zones:
            provider_data["zones"] = [str(zone) for zone in context.template.zones]

        return ProviderResult.success_result(
            {
                "resource_ids": [mig_name],
                "instances": [],
                "provider_api": provider_api,
                "count": context.count,
                "template_id": context.template.template_id,
                "failed_operations": [],
                "results": {mig_name: True},
            },
            {
                "operation": "create_instances",
                "handler_used": provider_api,
                "method": "dry_run",
                "provider_data": provider_data,
                "partial_failure": False,
            },
        )

    @staticmethod
    def build_provider_result(
        *,
        context: GCPCreateOperationContext,
        outcome: GCPCreateOutcome,
    ) -> ProviderResult:
        """Convert a provider-native acquire outcome into the ORB result schema."""
        failed_operations = outcome.failed_operations
        results = {
            **{instance["instance_id"]: True for instance in outcome.instances},
            **{failure.target_id: False for failure in failed_operations},
        }
        return ProviderResult.success_result(
            {
                "resource_ids": outcome.resource_ids,
                "instances": outcome.instances,
                "provider_api": context.template.provider_api.value,
                "count": context.count,
                "template_id": context.template.template_id,
                "failed_operations": [failure.__dict__ for failure in failed_operations],
                "results": results,
            },
            {
                "operation": "create_instances",
                "handler_used": context.template.provider_api.value,
                "provider_data": outcome.provider_data,
                "partial_failure": bool(failed_operations),
            },
        )
