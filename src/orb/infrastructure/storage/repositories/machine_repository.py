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
    """

    # Fields produced by model_dump that must not be written to storage.
    _DUMP_EXCLUDED: set[str] = set(Machine._SERIALIZATION_EXCLUDED_FIELDS)

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
        """Deserialize a storage dict back to a Machine aggregate."""
        try:
            data = self._normalize_on_read(data)
            return Machine.model_validate(data)
        except Exception as e:
            self.logger.error("Failed to deserialize machine data: %s", e)
            raise

    def _normalize_on_read(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normalize storage data before model_validate.

        Runs on every read to handle legacy data quirks and future
        schema evolution. Each fixup is idempotent.

        Categories:
          - FIELD MIGRATION: field was renamed or moved between schema versions
          - LEGACY DEFAULT:  field was absent in old records; the aggregate now
                             carries a Field(default=...) so these fixups are only
                             needed for fields whose storage default differs from
                             the aggregate default, or where the key must be
                             present for model_validate to accept the record.
        """
        data = dict(data)  # shallow copy — don't mutate the caller's dict

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

        # provider_type: Machine.provider_type now has Field(default="aws"), so
        # model_validate will supply the default when the key is absent.
        # No fixup needed here.

        return data


class MachineRepositoryImpl(StorageRepositoryMixin, MachineRepositoryInterface):
    """Single machine repository implementation using storage strategy composition."""

    def __init__(self, storage_strategy: BaseStorageStrategy) -> None:
        """Initialize repository with storage strategy."""
        if hasattr(storage_strategy, "entity_type"):
            storage_strategy.entity_type = "machines"  # type: ignore[attr-defined]

        self.storage_strategy = storage_strategy
        self.serializer = MachineSerializer()
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

    @handle_infrastructure_exceptions(context="machine_repository_find_by_request_id")
    def find_by_request_id(self, request_id: str) -> list[Machine]:
        """Find machines by request ID."""
        try:
            # Filter to only machine records (must have machine_id field)
            data_list = self._get_storage().find_by_criteria({"request_id": request_id})
            return [self.serializer.from_dict(d) for d in data_list if "machine_id" in d]  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find machines by request_id %s: %s", request_id, e)
            raise

    @handle_infrastructure_exceptions(context="machine_repository_find_by_return_request_id")
    def find_by_return_request_id(self, return_request_id: str) -> list[Machine]:
        """Find machines by return request ID."""
        try:
            data_list = self._get_storage().find_by_criteria(
                {"return_request_id": return_request_id}
            )
            return [self.serializer.from_dict(d) for d in data_list if "machine_id" in d]  # type: ignore[return-value]
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
