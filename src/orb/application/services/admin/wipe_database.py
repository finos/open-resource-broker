"""WipeDatabaseService — truncates all ORB data tables.

Deliberately destructive.  Only callable when allow_destructive_admin=true
and the environment is not production.  This service is invoked exclusively
by the admin router which enforces those guards before reaching here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from orb.domain.base import UnitOfWorkFactory

logger = logging.getLogger(__name__)


@dataclass
class WipeResult:
    """Result of a database wipe operation."""

    tables_truncated: list[str] = field(default_factory=list)
    rows_deleted: int = 0


def _bulk_delete(repo: object, id_field: str, entities: list) -> int:
    """Delete all *entities* from *repo* using bulk delete when available.

    Tries ``repo.storage_strategy.delete_batch(ids)`` first (single round-trip
    for both the JSON and SQL backends).  Falls back to per-entity
    ``repo.delete(entity)`` so the method is safe for any storage backend.

    Returns the number of rows deleted.
    """
    if not entities:
        return 0

    # Extract raw string IDs — each entity carries its PK as an attribute
    # whose name is given by *id_field* (e.g. "machine_id", "request_id").
    ids: list[str] = []
    for entity in entities:
        pk = getattr(entity, id_field, None)
        if pk is None:
            continue
        # Value objects expose .value; plain strings are used directly.
        ids.append(str(pk.value) if hasattr(pk, "value") else str(pk))

    # Fast path: delegate to delete_batch on the underlying storage strategy.
    storage = getattr(repo, "storage_strategy", None)
    if storage is not None and hasattr(storage, "delete_batch"):
        storage.delete_batch(ids)
        return len(ids)

    # Slow path: per-entity delete via the repository interface.
    for entity in entities:
        pk = getattr(entity, id_field)
        repo.delete(pk)  # type: ignore[attr-defined]
    return len(entities)


class WipeDatabaseService:
    """Truncates all ORB data repositories in a single bulk operation per table.

    Deliberately avoids DROP / raw SQL so that schema migrations remain intact
    and the application can continue running after the wipe without a restart.

    Uses the ``UnitOfWorkFactory`` rather than DI singleton repositories so the
    wipe targets the SAME storage instance the read/write paths use. Singleton
    repos are created with a ``generic`` entity_type bucket which doesn't
    match the per-entity buckets (``machines``/``requests``/``templates``) the
    UoW writes to — wiping via singletons silently no-ops on single-file
    JSON storage.

    Repositories wiped (in safe deletion order — children before parents):
      1. machines  — individual machine records
      2. requests  — request aggregates (machines reference requests)
      3. templates — template definitions

    Return requests are stored as Request aggregates with type RETURN so they
    are covered by the requests repository wipe.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
    ) -> None:
        self._uow_factory = uow_factory

    def execute(self) -> WipeResult:
        """Truncate all repositories and return a summary of what was deleted."""
        result = WipeResult()

        with self._uow_factory.create_unit_of_work() as uow:
            # 1. Machines
            try:
                machines = uow.machines.find_all()
                count = _bulk_delete(uow.machines, "machine_id", machines)
                result.tables_truncated.append("machines")
                result.rows_deleted += count
                logger.warning("ADMIN_WIPE: deleted %d machine record(s)", count)
            except Exception:
                logger.exception("ADMIN_WIPE: failed to delete machines")
                raise

            # 2. Requests (includes return requests — share the Request aggregate)
            try:
                requests = uow.requests.find_all()
                count = _bulk_delete(uow.requests, "request_id", requests)
                result.tables_truncated.append("requests")
                result.rows_deleted += count
                logger.warning("ADMIN_WIPE: deleted %d request record(s)", count)
            except Exception:
                logger.exception("ADMIN_WIPE: failed to delete requests")
                raise

            # 3. Templates
            try:
                templates = uow.templates.find_all()
                count = _bulk_delete(uow.templates, "template_id", templates)
                result.tables_truncated.append("templates")
                result.rows_deleted += count
                logger.warning("ADMIN_WIPE: deleted %d template record(s)", count)
            except Exception:
                logger.exception("ADMIN_WIPE: failed to delete templates")
                raise

        logger.warning(
            "ADMIN_WIPE: complete — %d total rows deleted across tables: %s",
            result.rows_deleted,
            result.tables_truncated,
        )
        return result
