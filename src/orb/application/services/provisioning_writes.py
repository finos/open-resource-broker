"""Writes the create flow makes to a request it is provisioning.

The create flow saves the request once before it calls the provider and then
keeps working on its in-memory copy for the length of the provider calls. Other
writers can save the same row meanwhile: a status read or the background
reconcile pass stamps ``first/last_status_check``, a cancel moves it to a
terminal status. Saving the in-memory copy as is would either be rejected by a
version-checked store (SQL), losing the resource and machine ids of capacity
that was already launched, or overwrite those writes (JSON).

So every create-flow write re-reads the stored row inside the write and applies
the in-memory state on top of it: the create flow's own fields win, the
status-check stamps and metadata written meanwhile are kept, resource and
machine ids are never dropped, and a terminal status reached meanwhile stands.
A conflict is met with one re-read and re-apply.
"""

from __future__ import annotations

from typing import Any, Optional

from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.request.aggregate import Request

_WRITE_ATTEMPTS = 2

# Fields describing where the request stands. When another writer has already
# taken the request to a terminal status, these are kept from the stored row.
_OUTCOME_FIELDS = (
    "status",
    "status_message",
    "completed_at",
    "last_transition_at",
    "partial_since",
    "successful_count",
    "failed_count",
    "fulfilment_diagnostic",
    "error_details",
)


def _union(first: list[str], second: list[str]) -> list[str]:
    return list(dict.fromkeys([*first, *second]))


def apply_onto_stored(request: Request, stored: Optional[Request]) -> Request:
    """Return ``request`` rebuilt on top of ``stored`` as one version step."""
    if stored is None:
        return request
    update: dict[str, Any] = {
        "resource_ids": _union(stored.resource_ids, request.resource_ids),
        "machine_ids": _union(stored.machine_ids, request.machine_ids),
        "first_status_check": stored.first_status_check or request.first_status_check,
        "last_status_check": stored.last_status_check or request.last_status_check,
        # Set once, when the request first leaves PENDING.
        "deadline_at": stored.deadline_at or request.deadline_at,
        # Keys written meanwhile (e.g. the last provider verdict) are kept;
        # the create flow's own keys win.
        "metadata": {**stored.metadata, **request.metadata},
        "provider_data": {**stored.provider_data, **request.provider_data},
        "version": stored.version + 1,
    }
    terminal_meanwhile = stored.status.is_terminal()
    if terminal_meanwhile:
        update.update({name: getattr(stored, name) for name in _OUTCOME_FIELDS})
    merged = request.model_copy(update=update)
    if terminal_meanwhile:
        # The transitions queued on the in-memory copy did not take effect.
        merged.clear_domain_events()
    return merged


def save_provisioning_state(
    uow_factory: UnitOfWorkFactory, request: Request
) -> tuple[Request, list[Any]]:
    """Persist the create flow's ``request`` without losing concurrent writes.

    Returns the request as saved and the domain events the save released.
    Raises ``ConcurrencyError`` when the row changes again between the re-read
    and the save on the second attempt as well.
    """
    for attempt in range(_WRITE_ATTEMPTS):
        try:
            with uow_factory.create_unit_of_work() as uow:
                stored = uow.requests.get_by_id(request.request_id)
                merged = apply_onto_stored(request, stored)
                events = uow.requests.save(merged)
            return merged, list(events or [])
        except ConcurrencyError:
            if attempt + 1 >= _WRITE_ATTEMPTS:
                raise
    raise AssertionError("unreachable")  # pragma: no cover
