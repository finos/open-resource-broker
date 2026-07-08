"""CycleCloud request-context resolution owned by the Azure provider."""

from __future__ import annotations

from orb.providers.base.strategy import ProviderOperation


def resolve_cyclecloud_request_metadata(
    *,
    operation: ProviderOperation,
) -> dict[str, object]:
    """Return request metadata supplied with the current operation."""
    request_metadata = dict(operation.parameters.get("request_metadata") or {})
    return request_metadata
