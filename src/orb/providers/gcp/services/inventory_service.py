"""GCP inventory/read result shaping helpers."""

from __future__ import annotations

from orb.providers.base.strategy import ProviderResult
from orb.providers.gcp.types import GCPInstanceStatus


class GCPInventoryService:
    """Own result shaping for GCP read/status operations."""

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
