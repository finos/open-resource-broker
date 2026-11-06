"""Resolve persisted provider coordinates for machines."""

from __future__ import annotations

from typing import Any, Optional

from orb.domain.base import UnitOfWorkFactory
from orb.domain.request.value_objects import RequestId


def _iso(moment: Any) -> Optional[str]:
    return moment.isoformat() if moment is not None else None


def load_machine_coordinates(
    uow_factory: UnitOfWorkFactory, machine_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Return per-machine provider coordinates keyed by machine id.

    Each entry carries ``provider_api``, ``resource_id``, ``provider_data`` and
    ``launched_at`` (ISO timestamp of the launch, or of the record's creation).
    Placement fields missing from a machine's own ``provider_data`` are filled
    from the request that originally provisioned it, so flows driven by a
    return request (which has no placement of its own) can still address the
    right resources.
    """
    if not machine_ids:
        return {}

    coordinates: dict[str, dict[str, Any]] = {}
    with uow_factory.create_unit_of_work() as uow:
        origin_data: dict[str, dict[str, Any]] = {}
        for machine in uow.machines.find_by_ids(machine_ids):
            provider_data = dict(machine.provider_data or {})
            origin_id = machine.request_id
            if origin_id:
                if origin_id not in origin_data:
                    origin = uow.requests.get_by_id(RequestId(value=origin_id))
                    origin_data[origin_id] = dict(origin.provider_data or {}) if origin else {}
                provider_data = {**origin_data[origin_id], **provider_data}
            coordinates[str(machine.machine_id.value)] = {
                "provider_api": machine.provider_api,
                "resource_id": machine.resource_id,
                "provider_data": provider_data,
                "launched_at": _iso(machine.launch_time or machine.created_at),
            }
    return coordinates
