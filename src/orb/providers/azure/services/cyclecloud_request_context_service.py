"""CycleCloud request-context resolution owned by the Azure provider."""

from __future__ import annotations

from typing import Callable, Optional

from orb.application.services.request_follow_up_context import get_request_follow_up_context
from orb.domain.base import UnitOfWorkFactory
from orb.domain.request.aggregate import Request
from orb.providers.base.strategy import ProviderOperation


CycleCloudRequestLookup = Callable[[str], Request | None]


def create_cyclecloud_request_lookup(
    uow_factory: UnitOfWorkFactory,
) -> CycleCloudRequestLookup:
    """Return a loader for request aggregates by request_id string."""

    def _lookup(request_id: str) -> Request | None:
        with uow_factory.create_unit_of_work() as uow:
            return uow.requests.find_by_request_id(str(request_id))

    return _lookup


def resolve_cyclecloud_request_metadata(
    *,
    operation: ProviderOperation,
    lookup_request_by_id: Optional[CycleCloudRequestLookup],
) -> dict[str, object]:
    """Merge operation request metadata with durable origin-request follow-up context."""
    request_metadata = dict(operation.parameters.get("request_metadata") or {})
    if lookup_request_by_id is None:
        return request_metadata

    request_id = operation.parameters.get("request_id") or (
        operation.context.get("request_id") if operation.context else None
    )
    if request_id in (None, ""):
        return request_metadata

    origin_request = lookup_request_by_id(str(request_id))
    if not origin_request:
        return request_metadata

    return {**get_request_follow_up_context(origin_request), **request_metadata}
