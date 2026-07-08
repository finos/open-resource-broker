"""Single machine repository implementation using storage strategy composition."""

from typing import Any, Optional

from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.repository import MachineRepository as MachineRepositoryInterface
from orb.domain.machine.value_objects import MachineStatus
from orb.infrastructure.error.decorators import handle_infrastructure_exceptions
from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.storage.base.repository_mixin import StorageRepositoryMixin
from orb.infrastructure.storage.base.strategy import BaseStorageStrategy
from orb.infrastructure.storage.components.entity_serializer import BaseEntitySerializer


class MachineSerializer(BaseEntitySerializer):
    """Handles Machine aggregate serialization/deserialization.

    Thin wrapper around Machine.model_dump / Machine.model_validate.
    Value objects (MachineId, InstanceType, Tags, …) carry @model_serializer /
    @model_validator so they self-flatten to/from plain scalars — no hand-rolling
    needed here.

    An optional ``storage_backend`` may be passed at construction time so the
    serializer can attempt to backfill ``provider_api`` from the source request
    when the column is NULL in a legacy row.
    """

    # Fields produced by model_dump that must not be written to storage.
    _DUMP_EXCLUDED: set[str] = set(Machine._SERIALIZATION_EXCLUDED_FIELDS)

    def __init__(self, storage_backend: Any = None) -> None:
        """Initialise serializer with an optional storage backend for backfill."""
        super().__init__()
        self._storage_backend = storage_backend

    def to_dict(self, machine: Machine) -> dict[str, Any]:  # type: ignore[override]
        """Serialize Machine to a storage-compatible dict."""
        try:
            data = machine.model_dump(mode="json", exclude=self._DUMP_EXCLUDED)
            data["schema_version"] = "2.0.0"
            return data
        except Exception as e:
            self.logger.error("Failed to serialize machine %s: %s", machine.machine_id, e)
            raise

    def from_dict(self, data: dict[str, Any]) -> Machine:
        """Deserialize a storage dict back to a Machine aggregate.

        Raises ValueError on invariant violations (e.g. missing
        provider_api) so callers can decide whether to skip-and-log a
        bad row or surface the failure. List endpoints catch and skip;
        single-id lookups propagate so a request for a known-bad id
        returns 404 instead of pretending the row exists.
        """
        try:
            data = self._normalize_on_read(data)
            return Machine.model_validate(data)
        except Exception as e:
            machine_id = data.get("machine_id", "<unknown>")
            self.logger.error("Failed to deserialize machine %s: %s", machine_id, e)
            raise

    _CURRENT_SCHEMA_VERSION = "2.0.0"

    @staticmethod
    def _apply_nullable_defaults(data: dict[str, Any]) -> dict[str, Any]:
        """Coerce nullable JSON columns to safe empty containers.

        Applied unconditionally — including on the schema_version fast path —
        so legacy NULL values stored before the NOT NULL migration are never
        handed to model_validate as Python None for fields that expect a dict
        or list.

        Fields coerced:
          - tags             → {} if None  (serialised as JSON in SQL)
          - metadata         → {} if None
          - provider_data    → {} if None
          - security_group_ids → [] if None
          - health_checks    → [] if None  (SQL column exists even though the
                               field is not on the Machine aggregate; coerce to
                               drop it harmlessly rather than propagate None)

        provider_api is handled separately: if None or empty, the caller
        attempts a fallback from the source request.  If no fallback is
        available the key is removed and model_validate raises ValidationError,
        which _safe_deserialize_iter catches, logs at ERROR, and skips.
        """
        if data.get("tags") is None:
            data["tags"] = {}
        if data.get("metadata") is None:
            data["metadata"] = {}
        if data.get("provider_data") is None:
            data["provider_data"] = {}
        if data.get("security_group_ids") is None:
            data["security_group_ids"] = []
        if data.get("health_checks") is None:
            data["health_checks"] = []
        return data

    def _normalize_on_read(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normalize storage data before model_validate.

        _apply_nullable_defaults runs first on every record (including the
        fast path) so NULL values in optional JSON columns never reach
        model_validate as None.

        The legacy fixup block runs only on records whose schema_version is
        absent or older than the current version.

        Categories:
          - FIELD MIGRATION: field was renamed or moved between schema versions
          - LEGACY DEFAULT:  field was absent in old records; the aggregate now
                             carries a Field(default=...) so these fixups are only
                             needed for fields whose storage default differs from
                             the aggregate default, or where the key must be
                             present for model_validate to accept the record.
        """
        data = dict(data)  # shallow copy — never mutate the caller's dict
        data = self._apply_nullable_defaults(data)

        # provider_api backfill: the domain requires this field (no default)
        # and rejects empty strings (Field min_length=1).
        # If it is absent or empty, attempt to recover from the source request
        # stored in the same row.  If recovery fails, do NOT set a sentinel
        # value — leave provider_api absent so model_validate raises a
        # ValidationError.  _safe_deserialize_iter will log at ERROR with the
        # machine_id and skip the row rather than emit a machine that would
        # silently bypass deprovisioning.
        if not data.get("provider_api"):
            recovered = self._backfill_provider_api(data)
            if recovered:
                data["provider_api"] = recovered
            else:
                machine_id = data.get("machine_id", "<unknown>")
                self.logger.error(
                    "Machine %s has no provider_api and no source request to backfill from. "
                    "This row will be skipped by list operations. "
                    "Run the backfill migration or update the record manually.",
                    machine_id,
                )
                # Remove the key entirely so model_validate raises ValidationError,
                # which _safe_deserialize_iter will catch, log, and skip.
                data.pop("provider_api", None)

        # Fast path: current-version records skip all legacy field-migration fixups.
        if data.get("schema_version") == self._CURRENT_SCHEMA_VERSION:
            return data

        # FIELD MIGRATION: legacy records may not have a name field; use
        # machine_id as a stand-in so the aggregate's Optional[str] name stays
        # meaningful rather than blank.
        if not data.get("name"):
            data["name"] = data.get("machine_id", "")

        # FIELD MIGRATION: tags were stored under metadata.tags in very old
        # records before the top-level tags field was introduced.
        if not data.get("tags"):
            legacy_tags = (data.get("metadata") or {}).get("tags")
            if legacy_tags:
                data["tags"] = legacy_tags

        # FIELD MIGRATION: vcpus, availability_zone, and region were written to
        # metadata by the AWS adapter before the provider_data consolidation.
        # Move them on-read so the aggregate always sees them in provider_data.
        # Idempotent: only migrates a key when provider_data does not already
        # have it, so re-reading a migrated record is a no-op.
        _metadata = dict(data.get("metadata") or {})  # copy — do not mutate caller's dict
        _provider_data = dict(data.get("provider_data") or {})
        _migrated = False
        for _key in ("vcpus", "availability_zone", "region"):
            if _key in _metadata and _key not in _provider_data:
                _provider_data[_key] = _metadata.pop(_key)
                _migrated = True
        if _migrated:
            data["metadata"] = _metadata
            data["provider_data"] = _provider_data

        # FIELD MIGRATION: legacy records may carry a nested provider_data
        # envelope written by the old adapter:
        #   {"method": "...", "provider_data": {"target_units": 3, ...}}
        # Promote nested keys to the top level and drop the redundant wrapper.
        # Idempotent: flat records pass through unchanged.
        _pd = data.get("provider_data")
        if isinstance(_pd, dict) and isinstance(_pd.get("provider_data"), dict):
            _nested = _pd.pop("provider_data")
            # Outer keys win over inner keys on collision.
            _pd = {**_nested, **_pd}
            data["provider_data"] = _pd

        # provider_type: Machine.provider_type now has Field(default="aws"), so
        # model_validate will supply the default when the key is absent.
        # No fixup needed here.

        return data

    def _backfill_provider_api(self, data: dict[str, Any]) -> str | None:
        """Attempt to recover provider_api from the machine's source request.

        Looks up the request_id stored on the machine row and reads
        provider_api from the corresponding request record.  Returns the
        recovered value or None when no source is available.

        Requires a ``storage_backend`` reference injected at construction
        time (set by ``MachineRepositoryImpl``).  When no backend is
        available the method returns None and the caller logs a WARNING.

        This is a read-time best-effort heuristic; the canonical fix is to
        run the backfill data migration.
        """
        if self._storage_backend is None:
            return None
        request_id = data.get("request_id")
        if not request_id:
            return None
        try:
            request_data = self._storage_backend.find_by_id(request_id)
            if isinstance(request_data, dict):
                value = request_data.get("provider_api")
                if value:
                    return str(value)
        except Exception as exc:
            # Best-effort backfill heuristic — the source request row may not
            # exist (e.g. purged) or the storage call may transiently fail.
            # Callers treat None as "unknown" and degrade gracefully.
            self.logger.debug("provider_api backfill heuristic skipped: %s", exc)
            return None
        return None


class MachineRepositoryImpl(StorageRepositoryMixin, MachineRepositoryInterface):
    """Single machine repository implementation using storage strategy composition."""

    def __init__(self, storage_strategy: BaseStorageStrategy) -> None:
        """Initialize repository with storage strategy."""
        if hasattr(storage_strategy, "entity_type"):
            storage_strategy.entity_type = "machines"  # type: ignore[attr-defined]

        self.storage_strategy = storage_strategy
        # Pass the storage backend so the serializer can backfill
        # provider_api from the source request on legacy rows.
        self.serializer = MachineSerializer(storage_backend=storage_strategy)
        self.logger = get_logger(__name__)

    @handle_infrastructure_exceptions(context="machine_repository_save")
    def save(self, machine: Machine) -> list[Any]:
        """Save machine using storage strategy and return extracted events."""
        try:
            machine_data = self.serializer.to_dict(machine)
            self.storage_strategy.save(str(machine.machine_id.value), machine_data)  # type: ignore[call-arg]

            events = machine.get_domain_events()
            machine.clear_domain_events()

            self.logger.debug(
                "Saved machine %s and extracted %s events",
                machine.machine_id,
                len(events),
            )
            return events

        except Exception as e:
            self.logger.error("Failed to save machine %s: %s", machine.machine_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_save_batch")
    def save_batch(self, machines: list[Machine]) -> list[Any]:
        """Save multiple machines in a single storage operation when supported."""
        try:
            if not machines:
                return []

            entity_batch: dict[str, dict[str, Any]] = {}
            events: list[Any] = []

            for machine in machines:
                entity_id = str(machine.machine_id.value)
                entity_batch[entity_id] = self.serializer.to_dict(machine)
                events.extend(machine.get_domain_events())

            if hasattr(self.storage_strategy, "save_batch"):
                self.storage_strategy.save_batch(entity_batch)  # type: ignore[attr-defined]
            else:
                # Fallback for storage strategies without batch support.
                for entity_id, machine_data in entity_batch.items():
                    self.storage_strategy.save(entity_id, machine_data)  # type: ignore[call-arg]

            # Clear domain events only after a successful storage call.
            for machine in machines:
                machine.clear_domain_events()

            self.logger.debug(
                "Saved batch of %s machines and extracted %s events",
                len(entity_batch),
                len(events),
            )
            return events

        except Exception as e:
            self.logger.error("Failed to save batch of %s machines: %s", len(machines), e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_get_by_id")
    def get_by_id(self, machine_id: MachineId | str) -> Optional[Machine]:
        """Get machine by ID using storage strategy."""
        try:
            id_str = str(machine_id.value) if isinstance(machine_id, MachineId) else str(machine_id)
            return self._load_by_id(id_str)  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to get machine %s: %s", machine_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_id")
    def find_by_id(self, machine_id: MachineId) -> Optional[Machine]:
        """Find machine by ID (alias for get_by_id)."""
        return self.get_by_id(machine_id)

    @handle_infrastructure_exceptions(context="machine_repository_find_by_instance_id")
    def find_by_instance_id(self, instance_id: MachineId) -> Optional[Machine]:
        """Find machine by instance ID (backward compatibility)."""
        try:
            results = self._load_by_criteria({"machine_id": str(instance_id.value)})
            return results[0] if results else None  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find machine by instance_id %s: %s", instance_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_machine_id")
    def find_by_machine_id(self, machine_id: MachineId) -> Optional[Machine]:
        """Find machine by machine ID."""
        try:
            results = self._load_by_criteria({"machine_id": str(machine_id.value)})
            return results[0] if results else None  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find machine by machine_id %s: %s", machine_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_template_id")
    def find_by_template_id(self, template_id: str) -> list[Machine]:
        """Find machines by template ID."""
        try:
            return self._load_by_criteria({"template_id": template_id})  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find machines by template_id %s: %s", template_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_status")
    def find_by_status(self, status: MachineStatus) -> list[Machine]:
        """Find machines by status."""
        try:
            return self._load_by_criteria({"status": status.value})  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find machines by status %s: %s", status, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_statuses")
    def find_by_statuses(self, statuses: list[MachineStatus]) -> list[Machine]:
        """Find machines by list of statuses."""
        try:
            all_machines = []
            for status in statuses:
                all_machines.extend(self.find_by_status(status))
            return all_machines
        except Exception as e:
            self.logger.error("Failed to find machines by statuses %s: %s", statuses, e)
            raise

    def _iter_deserialized_strict(self, data_list: list[dict[str, Any]]):
        """Deserialize each row and propagate any exception immediately.

        Use this on deprovisioning-critical paths (e.g. return-request
        machine lookups) where a skipped malformed row is semantically
        indistinguishable from a "machine already terminated" signal.
        Silently skipping a bad row on these paths would cause the
        return-request status poller to conclude all machines are gone and
        stamp the request COMPLETED prematurely.

        For list/dashboard endpoints that must never 500 on a single bad
        row, use the inherited ``_safe_deserialize_iter`` instead.
        """
        for data in data_list:
            # Delegates to self._deserialize (which calls serializer.from_dict)
            # and lets any exception propagate so the caller can surface it.
            yield self._deserialize(data)

    @handle_infrastructure_exceptions(context="machine_repository_find_by_request_id")
    def find_by_request_id(self, request_id: str) -> list[Machine]:
        """Find machines by request ID.

        Uses _safe_deserialize_iter — a single malformed row is logged and
        skipped rather than aborting the whole result set.  This is appropriate
        for list/dashboard usage where one bad row should not crash the page.
        For deprovisioning-critical lookups use find_by_return_request_id.
        """
        try:
            # Filter to only machine records (must have machine_id field).
            data_list = [
                d
                for d in self._get_storage().find_by_criteria({"request_id": request_id})
                if "machine_id" in d
            ]
            return list(self._safe_deserialize_iter(data_list))  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find machines by request_id %s: %s", request_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_return_request_id")
    def find_by_return_request_id(self, return_request_id: str) -> list[Machine]:
        """Find machines associated with a return request.

        Uses strict deserialization (_iter_deserialized_strict) rather than the
        safe-skip variant.  On deprovisioning paths a skipped malformed row is
        semantically indistinguishable from "machine already terminated" — the
        status poller would see fewer machines than expected and may prematurely
        stamp the return request COMPLETED.

        Any deserialization failure propagates as an exception so the caller
        can handle it explicitly (e.g. log at ERROR and leave the request
        IN_PROGRESS for the next poll cycle) rather than silently losing track
        of a machine.

        For list/dashboard endpoints that tolerate partial results, use
        find_by_request_id or _safe_deserialize_iter directly.
        """
        try:
            data_list = [
                d
                for d in self._get_storage().find_by_criteria(
                    {"return_request_id": return_request_id}
                )
                if "machine_id" in d
            ]
            return list(self._iter_deserialized_strict(data_list))  # type: ignore[return-value]
        except Exception as e:
            self.logger.error(
                "Failed to find machines by return_request_id %s: %s", return_request_id, e
            )
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_active_machines")
    def find_active_machines(self) -> list[Machine]:
        """Find all active (non-terminated) machines."""
        try:
            from orb.domain.machine.value_objects import MachineStatus

            active_statuses = [
                MachineStatus.PENDING,
                MachineStatus.RUNNING,
                MachineStatus.LAUNCHING,
            ]
            all_machines = []
            for status in active_statuses:
                all_machines.extend(self.find_by_status(status))
            return all_machines
        except Exception as e:
            self.logger.error("Failed to find active machines: %s", e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_ids")
    def find_by_ids(self, machine_ids: list[str]) -> list[Machine]:
        """Find machines by list of machine IDs."""
        try:
            machines = []
            for machine_id in machine_ids:
                machine = self.get_by_id(machine_id)
                if machine:
                    machines.append(machine)
            return machines
        except Exception as e:
            self.logger.error("Failed to find machines by IDs %s: %s", machine_ids, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_all")
    def find_all(self) -> list[Machine]:
        """Find all machines."""
        try:
            return self._load_all()  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find all machines: %s", e)
            raise

    def get_all(self) -> list[Machine]:
        """Return all machines from the repository."""
        return self.find_all()

    def count_by_status(self) -> dict[str, int]:
        """Return ``{status: count}`` for all machines.

        Delegates to ``storage_strategy.count_by_column("status")`` when the
        underlying strategy supports it (SQL fast path).  Falls back to the
        domain-interface default (list all + group) for file-based backends.
        """
        strategy = getattr(self, "storage_strategy", None)
        if strategy is not None and hasattr(strategy, "count_by_column"):
            result = strategy.count_by_column("status")
            if result:
                return result
        # Slow path: list all rows and group in Python.
        return super().count_by_status()

    @handle_infrastructure_exceptions(context="machine_repository_delete")
    def delete(self, machine_id: MachineId) -> None:
        """Delete machine by ID."""
        try:
            self._delete_by_id(str(machine_id.value))
            self.logger.debug("Deleted machine %s", machine_id)
        except Exception as e:
            self.logger.error("Failed to delete machine %s: %s", machine_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_exists")
    def exists(self, machine_id: MachineId) -> bool:
        """Check if machine exists."""
        try:
            return self._check_exists(str(machine_id.value))
        except Exception as e:
            self.logger.error("Failed to check if machine %s exists: %s", machine_id, e)
            raise
