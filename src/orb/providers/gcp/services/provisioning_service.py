"""GCP acquire/provisioning result shaping helpers."""

from __future__ import annotations

from orb.providers.base.strategy import ProviderResult
from orb.providers.gcp.types import GCPCreateOperationContext, GCPCreateOutcome


class GCPProvisioningService:
    """Own GCP create result shaping."""

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
