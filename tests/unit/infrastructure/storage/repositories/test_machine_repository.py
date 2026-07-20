"""Unit tests for machine_repository: MachineSerializer and MachineRepositoryImpl."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError as PydanticValidationError

from orb.domain.base.exceptions import InfrastructureError, ValidationError
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.value_objects import MachineStatus
from orb.infrastructure.storage.repositories.machine_repository import (
    MachineRepositoryImpl,
    MachineSerializer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MACHINE_ID = "i-0abc12345678def01"
_MACHINE_ID_2 = "i-0fed87654321cba02"
_REQUEST_ID = "req-8e6bf339-8207-45b6-ab3a-81ee4ab8abc6"


def _good_row(machine_id: str = _MACHINE_ID, status: str = "running") -> dict:
    return {
        "machine_id": machine_id,
        "name": f"machine-{machine_id}",
        "template_id": "tpl-001",
        "request_id": _REQUEST_ID,
        "provider_type": "aws",
        "provider_name": "aws-us-east-1",
        "provider_api": "RunInstances",
        "instance_type": "t2.micro",
        "image_id": "ami-00000000",
        "status": status,
        "schema_version": "2.0.0",
    }


def _make_machine(machine_id: str = _MACHINE_ID, status: str = "running") -> Machine:
    return Machine.model_validate(_good_row(machine_id=machine_id, status=status))


def _make_repo(storage: MagicMock | None = None):
    if storage is None:
        storage = MagicMock()
    return MachineRepositoryImpl(storage), storage


# ---------------------------------------------------------------------------
# MachineSerializer — _apply_nullable_defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineSerializerApplyNullableDefaults:
    """_apply_nullable_defaults coerces NULL JSON columns to empty containers."""

    def test_null_tags_coerced_to_empty_dict(self):
        result = MachineSerializer._apply_nullable_defaults({"tags": None})
        assert result["tags"] == {}

    def test_null_metadata_coerced_to_empty_dict(self):
        result = MachineSerializer._apply_nullable_defaults({"metadata": None})
        assert result["metadata"] == {}

    def test_null_provider_data_coerced_to_empty_dict(self):
        result = MachineSerializer._apply_nullable_defaults({"provider_data": None})
        assert result["provider_data"] == {}

    def test_null_security_group_ids_coerced_to_empty_list(self):
        result = MachineSerializer._apply_nullable_defaults({"security_group_ids": None})
        assert result["security_group_ids"] == []

    def test_null_health_checks_coerced_to_empty_list(self):
        result = MachineSerializer._apply_nullable_defaults({"health_checks": None})
        assert result["health_checks"] == []

    def test_existing_values_are_preserved(self):
        data = {
            "tags": {"env": "prod"},
            "metadata": {"key": "val"},
            "provider_data": {"fleet_id": "flx-001"},
            "security_group_ids": ["sg-001"],
        }
        result = MachineSerializer._apply_nullable_defaults(data)
        assert result["tags"] == {"env": "prod"}
        assert result["security_group_ids"] == ["sg-001"]


# ---------------------------------------------------------------------------
# MachineSerializer — _normalize_on_read legacy migration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineSerializerNormalizeOnRead:
    """_normalize_on_read applies legacy field migrations for older schema versions."""

    def _s(self):
        return MachineSerializer()

    def test_name_falls_back_to_machine_id_for_legacy_records(self):
        data = {"machine_id": "i-legacy001", "provider_api": "EC2Fleet"}
        result = self._s()._normalize_on_read(data)
        assert result.get("name") == "i-legacy001"

    def test_tags_migrated_from_metadata_tags(self):
        data = {
            "machine_id": "i-001",
            "provider_api": "RunInstances",
            "metadata": {"tags": {"env": "test"}},
            "tags": {},
        }
        result = self._s()._normalize_on_read(data)
        assert result["tags"] == {"env": "test"}

    def test_existing_tags_not_overwritten_by_metadata_tags(self):
        data = {
            "machine_id": "i-001",
            "provider_api": "RunInstances",
            "metadata": {"tags": {"env": "legacy"}},
            "tags": {"env": "current"},
        }
        result = self._s()._normalize_on_read(data)
        assert result["tags"] == {"env": "current"}

    def test_provider_type_defaults_to_aws_for_legacy_rows(self):
        data = {"machine_id": "i-001", "provider_api": "EC2Fleet"}
        result = self._s()._normalize_on_read(data)
        assert result["provider_type"] == "aws"

    def test_provider_type_not_overwritten_when_present(self):
        data = {
            "machine_id": "i-001",
            "provider_api": "K8sJob",
            "provider_type": "k8s",
            "schema_version": "2.0.0",
        }
        result = self._s()._normalize_on_read(data)
        assert result["provider_type"] == "k8s"

    def test_nested_provider_data_envelope_promoted(self):
        data = {
            "machine_id": "i-001",
            "provider_api": "EC2Fleet",
            "provider_data": {"method": "fleet", "provider_data": {"fleet_id": "flx-001"}},
        }
        result = self._s()._normalize_on_read(data)
        assert result["provider_data"].get("fleet_id") == "flx-001"
        assert "provider_data" not in result["provider_data"]

    def test_schema_version_2_0_0_skips_legacy_migration(self):
        """Current-schema rows bypass all legacy fixup branches (fast path)."""
        data = {
            "machine_id": "i-current",
            "provider_api": "RunInstances",
            "schema_version": "2.0.0",
            "provider_type": "aws",
        }
        result = self._s()._normalize_on_read(data)
        # provider_api intact, no crash
        assert result["provider_api"] == "RunInstances"

    def test_does_not_mutate_caller_dict(self):
        original = {
            "machine_id": "i-001",
            "provider_api": "EC2Fleet",
            "metadata": {"vcpus": 4},
            "provider_data": {},
        }
        original_metadata = dict(original["metadata"])
        self._s()._normalize_on_read(original)
        assert original["metadata"] == original_metadata


# ---------------------------------------------------------------------------
# MachineSerializer — _backfill_provider_api
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineSerializerBackfillProviderApi:
    """_backfill_provider_api recovers provider_api from the source request row."""

    def test_returns_none_when_no_storage_backend(self):
        s = MachineSerializer()
        result = s._backfill_provider_api({"machine_id": "i-001", "request_id": "req-001"})
        assert result is None

    def test_returns_none_when_no_request_id(self):
        backend = MagicMock()
        s = MachineSerializer(storage_backend=backend)
        result = s._backfill_provider_api({"machine_id": "i-001"})
        assert result is None
        backend.find_by_id.assert_not_called()

    def test_returns_provider_api_from_request_row(self):
        backend = MagicMock()
        backend.find_by_id.return_value = {"provider_api": "EC2Fleet"}
        s = MachineSerializer(storage_backend=backend)
        result = s._backfill_provider_api({"machine_id": "i-001", "request_id": "req-001"})
        assert result == "EC2Fleet"

    def test_returns_none_when_request_row_has_no_provider_api(self):
        backend = MagicMock()
        backend.find_by_id.return_value = {"template_id": "tpl-001"}
        s = MachineSerializer(storage_backend=backend)
        result = s._backfill_provider_api({"machine_id": "i-001", "request_id": "req-001"})
        assert result is None

    def test_returns_none_when_storage_raises(self):
        backend = MagicMock()
        backend.find_by_id.side_effect = RuntimeError("timeout")
        s = MachineSerializer(storage_backend=backend)
        result = s._backfill_provider_api({"machine_id": "i-001", "request_id": "req-001"})
        assert result is None

    def test_returns_none_when_request_row_is_none(self):
        backend = MagicMock()
        backend.find_by_id.return_value = None
        s = MachineSerializer(storage_backend=backend)
        result = s._backfill_provider_api({"machine_id": "i-001", "request_id": "req-001"})
        assert result is None


# ---------------------------------------------------------------------------
# MachineSerializer — to_dict / from_dict round-trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineSerializerRoundTrip:
    """to_dict followed by from_dict must reconstruct the same Machine."""

    def _s(self):
        return MachineSerializer()

    def test_round_trip_preserves_machine_id(self):
        machine = _make_machine()
        data = self._s().to_dict(machine)
        restored = self._s().from_dict(data)
        assert str(restored.machine_id) == _MACHINE_ID

    def test_round_trip_preserves_status(self):
        machine = _make_machine(status="running")
        data = self._s().to_dict(machine)
        restored = self._s().from_dict(data)
        assert restored.status == MachineStatus.RUNNING

    def test_round_trip_preserves_provider_api(self):
        machine = _make_machine()
        data = self._s().to_dict(machine)
        restored = self._s().from_dict(data)
        assert restored.provider_api == "RunInstances"

    def test_round_trip_preserves_instance_type(self):
        machine = _make_machine()
        data = self._s().to_dict(machine)
        restored = self._s().from_dict(data)
        assert str(restored.instance_type) == "t2.micro"

    def test_to_dict_schema_version_is_2_0_0(self):
        data = self._s().to_dict(_make_machine())
        assert data["schema_version"] == "2.0.0"

    def test_from_dict_raises_on_corrupt_row(self):
        """A row missing required fields must raise so callers can surface the failure."""
        bad = {"machine_id": "i-corrupt", "provider_api": "RunInstances"}
        with pytest.raises(PydanticValidationError, match="validation error"):
            self._s().from_dict(bad)


# ---------------------------------------------------------------------------
# MachineRepositoryImpl — save
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryImplSave:
    """save delegates to storage and extracts domain events."""

    def test_save_calls_storage_save(self):
        repo, storage = _make_repo()
        repo.save(_make_machine())
        assert storage.save.called

    def test_save_uses_machine_id_as_entity_key(self):
        repo, storage = _make_repo()
        repo.save(_make_machine())
        args = storage.save.call_args[0]
        entity_id, _ = args
        assert entity_id == _MACHINE_ID

    def test_save_returns_domain_events_list(self):
        repo, _ = _make_repo()
        events = repo.save(_make_machine())
        assert isinstance(events, list)

    def test_save_clears_domain_events_after_extraction(self):
        repo, _ = _make_repo()
        machine = _make_machine()
        repo.save(machine)
        assert machine.get_domain_events() == []

    def test_save_raises_on_storage_failure(self):
        repo, storage = _make_repo()
        storage.save.side_effect = RuntimeError("write failure")
        with pytest.raises(InfrastructureError, match="Unexpected error: write failure"):
            repo.save(_make_machine())


# ---------------------------------------------------------------------------
# MachineRepositoryImpl — save_batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryImplSaveBatch:
    """save_batch delegates to storage.save_batch when available, else falls back."""

    def _two_machines(self):
        return [_make_machine(_MACHINE_ID), _make_machine(_MACHINE_ID_2)]

    def test_empty_batch_returns_empty_events(self):
        repo, storage = _make_repo()
        events = repo.save_batch([])
        assert events == []
        storage.save.assert_not_called()

    def test_batch_save_uses_native_save_batch_when_supported(self):
        repo, storage = _make_repo()
        events = repo.save_batch(self._two_machines())
        assert storage.save_batch.called
        assert isinstance(events, list)

    def test_batch_save_falls_back_to_individual_saves(self):
        storage = MagicMock(
            spec=["find_by_id", "find_by_criteria", "find_all", "delete", "exists", "save"]
        )
        repo = MachineRepositoryImpl(storage)
        repo.save_batch(self._two_machines())
        assert storage.save.call_count == 2

    def test_batch_save_clears_events_after_successful_save(self):
        repo, _ = _make_repo()
        machines = self._two_machines()
        repo.save_batch(machines)
        for m in machines:
            assert m.get_domain_events() == []

    def test_batch_save_raises_on_storage_failure(self):
        repo, storage = _make_repo()
        storage.save_batch.side_effect = RuntimeError("batch fail")
        with pytest.raises(InfrastructureError, match="Unexpected error: batch fail"):
            repo.save_batch(self._two_machines())


# ---------------------------------------------------------------------------
# MachineRepositoryImpl — get_by_id / find_by_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryImplGetById:
    """get_by_id and find_by_id resolve a Machine from storage."""

    def test_get_by_id_machine_id_obj_returns_machine(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = _good_row()
        result = repo.get_by_id(MachineId(value=_MACHINE_ID))
        assert result is not None
        assert str(result.machine_id) == _MACHINE_ID

    def test_get_by_id_string_returns_machine(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = _good_row()
        result = repo.get_by_id(_MACHINE_ID)
        assert result is not None

    def test_get_by_id_returns_none_when_not_found(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = None
        result = repo.get_by_id(MachineId(value=_MACHINE_ID))
        assert result is None

    def test_find_by_id_delegates_to_get_by_id(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = _good_row()
        result = repo.find_by_id(MachineId(value=_MACHINE_ID))
        assert result is not None


# ---------------------------------------------------------------------------
# MachineRepositoryImpl — find_by_status / find_by_statuses
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryImplFindByStatus:
    """find_by_status and find_by_statuses pass correct criteria to storage."""

    def test_find_by_status_queries_correct_status_value(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = [_good_row()]
        repo.find_by_status(MachineStatus.RUNNING)
        storage.find_by_criteria.assert_called_once_with({"status": "running"})

    def test_find_by_status_returns_empty_when_no_results(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = []
        results = repo.find_by_status(MachineStatus.TERMINATED)
        assert results == []

    def test_find_by_statuses_queries_each_status(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = []
        repo.find_by_statuses([MachineStatus.PENDING, MachineStatus.RUNNING])
        assert storage.find_by_criteria.call_count == 2

    def test_find_by_statuses_combines_results(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.side_effect = [
            [_good_row(_MACHINE_ID, "pending")],
            [_good_row(_MACHINE_ID_2, "running")],
        ]
        results = repo.find_by_statuses([MachineStatus.PENDING, MachineStatus.RUNNING])
        assert len(results) == 2

    def test_find_active_machines_covers_pending_running_launching(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = []
        repo.find_active_machines()
        # Should query for three statuses
        assert storage.find_by_criteria.call_count == 3


# ---------------------------------------------------------------------------
# MachineRepositoryImpl — find_by_request_id (safe skip)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryImplFindByRequestId:
    """find_by_request_id uses safe deserialization — bad rows are skipped."""

    def test_returns_valid_machines_skipping_bad_rows(self):
        repo, storage = _make_repo()
        bad_row = {"machine_id": "i-corrupt", "request_id": _REQUEST_ID}  # missing required fields
        storage.find_by_criteria.return_value = [_good_row(), bad_row]
        results = repo.find_by_request_id(_REQUEST_ID)
        assert len(results) == 1
        assert str(results[0].machine_id) == _MACHINE_ID

    def test_rows_without_machine_id_key_are_excluded(self):
        """Rows that look like request records (no machine_id) are filtered out."""
        repo, storage = _make_repo()
        request_row = {"request_id": _REQUEST_ID, "status": "pending"}
        storage.find_by_criteria.return_value = [request_row]
        results = repo.find_by_request_id(_REQUEST_ID)
        assert results == []

    def test_returns_empty_when_no_machines_for_request(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = []
        results = repo.find_by_request_id(_REQUEST_ID)
        assert results == []


# ---------------------------------------------------------------------------
# MachineRepositoryImpl — find_by_return_request_id (strict)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryImplFindByReturnRequestId:
    """find_by_return_request_id raises on bad rows — never silently skips."""

    def test_good_rows_deserialize_successfully(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = [
            _good_row(_MACHINE_ID),
            _good_row(_MACHINE_ID_2),
        ]
        results = repo.find_by_return_request_id("ret-001")
        assert len(results) == 2

    def test_corrupt_row_propagates_exception(self):
        repo, storage = _make_repo()
        corrupt = {"machine_id": "i-corrupt", "return_request_id": "ret-001"}
        storage.find_by_criteria.return_value = [corrupt]
        with pytest.raises(ValidationError, match="validation error"):
            repo.find_by_return_request_id("ret-001")

    def test_rows_without_machine_id_are_filtered_before_deserialization(self):
        """Non-machine rows (missing machine_id key) are excluded before strict deserialization."""
        repo, storage = _make_repo()
        request_row = {"request_id": _REQUEST_ID}
        good = _good_row()
        storage.find_by_criteria.return_value = [request_row, good]
        results = repo.find_by_return_request_id("ret-001")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# MachineRepositoryImpl — count_by_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryImplCountByStatus:
    """count_by_status uses SQL fast path when available, else slow path."""

    def test_fast_path_delegates_to_count_by_column(self):
        repo, storage = _make_repo()
        storage.count_by_column.return_value = {"running": 10, "terminated": 3}
        counts = repo.count_by_status()
        assert counts == {"running": 10, "terminated": 3}

    def test_slow_path_used_when_count_by_column_absent(self):
        storage = MagicMock(
            spec=["find_by_id", "find_by_criteria", "find_all", "delete", "exists", "save"]
        )
        storage.find_all.return_value = []
        repo = MachineRepositoryImpl(storage)
        counts = repo.count_by_status()
        assert isinstance(counts, dict)

    def test_fast_path_falls_back_when_count_by_column_returns_empty(self):
        repo, storage = _make_repo()
        storage.count_by_column.return_value = {}
        storage.find_all.return_value = []
        counts = repo.count_by_status()
        assert isinstance(counts, dict)


# ---------------------------------------------------------------------------
# MachineRepositoryImpl — delete / exists / find_by_ids / find_all
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryImplDeleteAndExists:
    """delete, exists, find_by_ids, and find_all delegate to storage correctly."""

    def test_delete_calls_storage_delete_with_machine_id_string(self):
        repo, storage = _make_repo()
        mid = MachineId(value=_MACHINE_ID)
        repo.delete(mid)
        storage.delete.assert_called_once_with(_MACHINE_ID)

    def test_exists_returns_true_when_storage_says_so(self):
        repo, storage = _make_repo()
        storage.exists.return_value = True
        assert repo.exists(MachineId(value=_MACHINE_ID)) is True

    def test_exists_returns_false_when_machine_absent(self):
        repo, storage = _make_repo()
        storage.exists.return_value = False
        assert repo.exists(MachineId(value=_MACHINE_ID)) is False

    def test_find_by_ids_returns_found_machines_only(self):
        repo, storage = _make_repo()
        storage.find_by_id.side_effect = [_good_row(), None]
        results = repo.find_by_ids([_MACHINE_ID, "i-missing00000001"])
        assert len(results) == 1
        assert str(results[0].machine_id) == _MACHINE_ID

    def test_find_by_ids_empty_input_returns_empty_list(self):
        repo, _ = _make_repo()
        results = repo.find_by_ids([])
        assert results == []

    def test_find_all_loads_all_rows_from_storage(self):
        repo, storage = _make_repo()
        storage.find_all.return_value = [_good_row(_MACHINE_ID), _good_row(_MACHINE_ID_2)]
        results = repo.find_all()
        assert len(results) == 2

    def test_get_all_is_alias_for_find_all(self):
        repo, storage = _make_repo()
        storage.find_all.return_value = [_good_row()]
        assert repo.get_all() == repo.find_all()


# ---------------------------------------------------------------------------
# MachineRepositoryImpl — find_by_machine_id / find_by_instance_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryImplFindByMachineId:
    """find_by_machine_id and find_by_instance_id use criteria-based lookup."""

    def test_find_by_machine_id_returns_machine_when_found(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = [_good_row()]
        result = repo.find_by_machine_id(MachineId(value=_MACHINE_ID))
        assert result is not None

    def test_find_by_machine_id_returns_none_when_empty(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = []
        result = repo.find_by_machine_id(MachineId(value=_MACHINE_ID))
        assert result is None

    def test_find_by_instance_id_returns_machine_when_found(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = [_good_row()]
        result = repo.find_by_instance_id(MachineId(value=_MACHINE_ID))
        assert result is not None
