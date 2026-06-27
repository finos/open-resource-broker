"""CleanupDatabaseService — hard-delete individual or bulk request/machine rows.

Deliberately destructive.  Only callable when allow_destructive_admin=true
and the environment is not production.  This service is invoked exclusively
by the admin/requests/machines routers which enforce those guards before
reaching here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from orb.domain.base import UnitOfWorkFactory

logger = logging.getLogger(__name__)

# Non-terminal request status strings — the bulk cleanup body rejects
# these. Enum-form constants were removed: the cleanup paths normalise
# status to strings before checking, so the enum sets were never read.
_NON_TERMINAL_REQUEST_STATUS_STRINGS = {
    "pending",
    "in_progress",
    "acquiring",
}


class NonTerminalStatusError(ValueError):
    """Raised when a purge is attempted on a non-terminal record."""


class InvalidCleanupStatusError(ValueError):
    """Raised when the cleanup body lists a non-terminal or unknown status."""


@dataclass
class CleanupResult:
    """Result of a cleanup (single-row or bulk) operation."""

    requests_deleted: int = 0
    machines_deleted: int = 0
    details: list[str] = field(default_factory=list)


class CleanupDatabaseService:
    """Hard-delete individual or bulk request/machine rows via the UoW pattern.

    Uses ``UnitOfWorkFactory`` so the deletes target the same storage instance
    and per-entity buckets that the read/write paths use — matching the pattern
    established in ``WipeDatabaseService``.

    Public methods
    --------------
    delete_request(request_id, cascade_machines=True) -> CleanupResult
        Hard-delete a single request (must be terminal) and optionally cascade.

    delete_machine(machine_id) -> CleanupResult
        Hard-delete a single machine (must be terminal).

    bulk_cleanup(statuses, older_than_days=None, include_machines=True) -> CleanupResult
        Delete all requests matching the status filter (and optional age filter).
    """

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    # ------------------------------------------------------------------
    # Per-row helpers
    # ------------------------------------------------------------------

    def delete_request(
        self,
        request_id: str,
        cascade_machines: bool = True,
    ) -> CleanupResult:
        """Hard-delete a single request row and optionally its machine rows.

        Raises
        ------
        NonTerminalStatusError
            When the request is in a non-terminal state (pending / in_progress).
        KeyError
            When no request with the given ID exists.
        """
        result = CleanupResult()

        with self._uow_factory.create_unit_of_work() as uow:
            request = uow.requests.find_by_request_id(request_id)
            if request is None:
                raise KeyError(f"Request '{request_id}' not found.")

            if not request.status.is_terminal():
                raise NonTerminalStatusError(
                    f"Request '{request_id}' has non-terminal status '{request.status.value}'. "
                    "Cancel or fail the request before purging."
                )

            machines_deleted = 0
            if cascade_machines:
                machines = uow.machines.find_by_request_id(request_id)
                for machine in machines:
                    uow.machines.delete(machine.machine_id)
                    machines_deleted += 1
                    result.details.append(f"machine:{machine.machine_id}")

            uow.requests.delete(request_id)
            result.requests_deleted = 1
            result.machines_deleted = machines_deleted
            result.details.append(f"request:{request_id}")

            logger.warning(
                "ADMIN_CLEANUP: deleted request=%s cascade_machines=%s machines_deleted=%d",
                request_id,
                cascade_machines,
                machines_deleted,
            )

        return result

    def delete_machine(self, machine_id: str) -> CleanupResult:
        """Hard-delete a single machine row.

        Raises
        ------
        NonTerminalStatusError
            When the machine is in a non-terminal state.
        KeyError
            When no machine with the given ID exists.
        """
        result = CleanupResult()

        with self._uow_factory.create_unit_of_work() as uow:
            machine = uow.machines.get_by_id(machine_id)
            if machine is None:
                raise KeyError(f"Machine '{machine_id}' not found.")

            if not machine.status.is_terminal:
                raise NonTerminalStatusError(
                    f"Machine '{machine_id}' has non-terminal status '{machine.status.value}'. "
                    "The machine must be in a terminal state (terminated, failed, returned) "
                    "before purging."
                )

            uow.machines.delete(machine_id)
            result.machines_deleted = 1
            result.details.append(f"machine:{machine_id}")

            logger.warning(
                "ADMIN_CLEANUP: deleted machine=%s",
                machine_id,
            )

        return result

    # ------------------------------------------------------------------
    # Bulk cleanup
    # ------------------------------------------------------------------

    def bulk_cleanup(
        self,
        statuses: list[str],
        older_than_days: Optional[int] = None,
        include_machines: bool = True,
        caller_id: str = "unknown",
    ) -> CleanupResult:
        """Delete all requests matching the given status filter.

        Parameters
        ----------
        statuses:
            List of request status strings to delete.  All must be terminal.
        older_than_days:
            When set, only delete requests whose ``created_at`` is older than
            this many days.  ``None`` means no age restriction.
        include_machines:
            Whether to cascade-delete associated machine rows.
        caller_id:
            Identity string used in the audit log.

        Raises
        ------
        InvalidCleanupStatusError
            When ``statuses`` is empty or contains a non-terminal status.
        """
        if not statuses:
            raise InvalidCleanupStatusError(
                "request_statuses must contain at least one terminal status."
            )

        normalised = [s.lower().strip() for s in statuses]
        bad = [s for s in normalised if s in _NON_TERMINAL_REQUEST_STATUS_STRINGS]
        if bad:
            raise InvalidCleanupStatusError(
                f"Non-terminal statuses are not allowed in bulk cleanup: {bad}. "
                "Only terminal statuses (cancelled, complete, failed, timeout, partial) "
                "may be targeted."
            )

        cutoff: Optional[datetime] = None
        if older_than_days is not None:
            cutoff = datetime.now(tz=timezone.utc) - timedelta(days=older_than_days)

        result = CleanupResult()

        with self._uow_factory.create_unit_of_work() as uow:
            all_requests = uow.requests.find_all()

            for request in all_requests:
                # Status filter
                if request.status.value not in normalised:
                    continue

                # Age filter (skip recent records if cutoff is set).
                # Rows with NULL created_at are treated as "too recent to
                # purge" — unknown age must never accidentally slip past the
                # cutoff and result in unintended deletion.
                if cutoff is not None:
                    created = request.created_at
                    if created is None:
                        continue
                    # Normalise to UTC-aware for comparison
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    if created >= cutoff:
                        continue

                machines_deleted = 0
                if include_machines:
                    machines = uow.machines.find_by_request_id(str(request.request_id))
                    for machine in machines:
                        uow.machines.delete(machine.machine_id)
                        machines_deleted += 1

                uow.requests.delete(request.request_id)
                result.requests_deleted += 1
                result.machines_deleted += machines_deleted

            logger.warning(
                "ADMIN_CLEANUP by user=%s statuses=%s older_than_days=%s "
                "include_machines=%s requests_deleted=%d machines_deleted=%d",
                caller_id,
                normalised,
                older_than_days,
                include_machines,
                result.requests_deleted,
                result.machines_deleted,
            )

        return result
