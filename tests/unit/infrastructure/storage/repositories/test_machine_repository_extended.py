"""Extended unit tests for MachineRepositoryImpl covering uncovered branches."""

from unittest.mock import MagicMock

import pytest

from orb.domain.base.exceptions import InfrastructureError, ValidationError
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.value_objects import MachineStatus
from orb.infrastructure.storage.repositories.machine_repository import (
    MachineRepositoryImpl,
)

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
        storage.find_by_id.return_value = None
        storage.find_by_criteria.return_value = []
        storage.find_all.return_value = {}
        storage.exists.return_value = False
    return MachineRepositoryImpl(storage), storage


# ---------------------------------------------------------------------------
# get_by_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryGetById:
    def test_returns_none_when_not_found(self) -> None:
        repo, _ = _make_repo()
        result = repo.get_by_id("missing")
        assert result is None

    def test_returns_machine_when_found(self) -> None:
        storage = MagicMock()
        storage.find_by_id.return_value = _good_row()
        repo, _ = _make_repo(storage)
        machine = repo.get_by_id(_MACHINE_ID)
        assert machine is not None
        assert str(machine.machine_id) == _MACHINE_ID

    def test_accepts_machine_id_value_object(self) -> None:
        storage = MagicMock()
        storage.find_by_id.return_value = _good_row()
        repo, _ = _make_repo(storage)
        machine = repo.get_by_id(MachineId(value=_MACHINE_ID))
        assert machine is not None

    def test_raises_on_storage_failure(self) -> None:
        storage = MagicMock()
        storage.find_by_id.side_effect = RuntimeError("backend down")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: backend down"):
            repo.get_by_id(_MACHINE_ID)


# ---------------------------------------------------------------------------
# find_by_id / find_by_instance_id / find_by_machine_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryFindById:
    def test_find_by_id_delegates_to_get_by_id(self) -> None:
        storage = MagicMock()
        storage.find_by_id.return_value = _good_row()
        repo, _ = _make_repo(storage)
        machine = repo.find_by_id(MachineId(value=_MACHINE_ID))
        assert machine is not None

    def test_find_by_instance_id_returns_machine(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.return_value = [_good_row()]
        repo, _ = _make_repo(storage)
        result = repo.find_by_instance_id(MachineId(value=_MACHINE_ID))
        assert result is not None

    def test_find_by_instance_id_returns_none_if_empty(self) -> None:
        repo, _ = _make_repo()
        result = repo.find_by_instance_id(MachineId(value="nope"))
        assert result is None

    def test_find_by_machine_id_returns_machine(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.return_value = [_good_row()]
        repo, _ = _make_repo(storage)
        result = repo.find_by_machine_id(MachineId(value=_MACHINE_ID))
        assert result is not None


# ---------------------------------------------------------------------------
# find_by_template_id / find_by_status / find_by_statuses
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryFindByTemplateIdAndStatus:
    def test_find_by_template_id_returns_list(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.return_value = [_good_row(), _good_row(_MACHINE_ID_2)]
        repo, _ = _make_repo(storage)
        result = repo.find_by_template_id("tpl-001")
        assert len(result) == 2

    def test_find_by_template_id_raises_on_storage_failure(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.side_effect = RuntimeError("fail")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: fail"):
            repo.find_by_template_id("tpl-001")

    def test_find_by_status_returns_matching(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.return_value = [_good_row(status="running")]
        repo, _ = _make_repo(storage)
        result = repo.find_by_status(MachineStatus.RUNNING)
        assert len(result) == 1

    def test_find_by_status_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.side_effect = RuntimeError("crash")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: crash"):
            repo.find_by_status(MachineStatus.RUNNING)

    def test_find_by_statuses_aggregates_all(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.side_effect = lambda c: [_good_row()]
        repo, _ = _make_repo(storage)
        result = repo.find_by_statuses([MachineStatus.RUNNING, MachineStatus.PENDING])
        assert len(result) == 2

    def test_find_by_statuses_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.side_effect = RuntimeError("crash")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: crash"):
            repo.find_by_statuses([MachineStatus.RUNNING])


# ---------------------------------------------------------------------------
# find_by_request_id / find_by_return_request_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryFindByRequestId:
    def test_find_by_request_id_returns_machines(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.return_value = [_good_row()]
        repo, _ = _make_repo(storage)
        result = repo.find_by_request_id(_REQUEST_ID)
        assert len(result) == 1

    def test_find_by_request_id_filters_non_machine_rows(self) -> None:
        storage = MagicMock()
        # Row without machine_id should be skipped
        storage.find_by_criteria.return_value = [{"request_id": _REQUEST_ID, "foo": "bar"}]
        repo, _ = _make_repo(storage)
        result = repo.find_by_request_id(_REQUEST_ID)
        assert result == []

    def test_find_by_request_id_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.side_effect = RuntimeError("crash")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: crash"):
            repo.find_by_request_id(_REQUEST_ID)

    def test_find_by_return_request_id_returns_machines(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.return_value = [_good_row()]
        repo, _ = _make_repo(storage)
        result = repo.find_by_return_request_id("ret-abc")
        assert len(result) == 1

    def test_find_by_return_request_id_filters_non_machine_rows(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.return_value = [{"return_request_id": "ret-xyz"}]
        repo, _ = _make_repo(storage)
        result = repo.find_by_return_request_id("ret-xyz")
        assert result == []

    def test_find_by_return_request_id_raises_on_deserialization_error(self) -> None:
        storage = MagicMock()
        # Row missing required fields — _iter_deserialized_strict propagates error
        storage.find_by_criteria.return_value = [{"machine_id": "i-bad"}]
        repo, _ = _make_repo(storage)
        with pytest.raises(ValidationError, match="validation error"):
            repo.find_by_return_request_id("ret-bad")


# ---------------------------------------------------------------------------
# find_active_machines / find_by_ids
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryFindActiveFindByIds:
    def test_find_active_machines_returns_union(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.side_effect = lambda c: [_good_row()]
        repo, _ = _make_repo(storage)
        result = repo.find_active_machines()
        # 3 active statuses × 1 machine each = 3
        assert len(result) == 3

    def test_find_active_machines_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.side_effect = RuntimeError("boom")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: boom"):
            repo.find_active_machines()

    def test_find_by_ids_returns_matching_machines(self) -> None:
        storage = MagicMock()
        storage.find_by_id.return_value = _good_row()
        repo, _ = _make_repo(storage)
        result = repo.find_by_ids([_MACHINE_ID, _MACHINE_ID_2])
        assert len(result) == 2

    def test_find_by_ids_skips_missing(self) -> None:
        repo, _ = _make_repo()
        result = repo.find_by_ids(["missing"])
        assert result == []

    def test_find_by_ids_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.find_by_id.side_effect = RuntimeError("boom")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: boom"):
            repo.find_by_ids([_MACHINE_ID])


# ---------------------------------------------------------------------------
# find_all / get_all / delete / exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryFindAllDeleteExists:
    def test_find_all_returns_list(self) -> None:
        storage = MagicMock()
        storage.find_all.return_value = {_MACHINE_ID: _good_row()}
        repo, _ = _make_repo(storage)
        result = repo.find_all()
        assert isinstance(result, list)

    def test_find_all_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.find_all.side_effect = RuntimeError("crash")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: crash"):
            repo.find_all()

    def test_get_all_is_alias_for_find_all(self) -> None:
        storage = MagicMock()
        storage.find_all.return_value = {}
        repo, _ = _make_repo(storage)
        assert repo.get_all() == repo.find_all()

    def test_delete_calls_storage_delete(self) -> None:
        storage = MagicMock()
        storage.exists.return_value = True
        repo, _ = _make_repo(storage)
        repo.delete(MachineId(value=_MACHINE_ID))
        storage.delete.assert_called()

    def test_delete_raises_on_storage_failure(self) -> None:
        storage = MagicMock()
        storage.delete.side_effect = RuntimeError("boom")
        storage.exists.return_value = True
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: boom"):
            repo.delete(MachineId(value=_MACHINE_ID))

    def test_exists_returns_true(self) -> None:
        storage = MagicMock()
        storage.exists.return_value = True
        repo, _ = _make_repo(storage)
        assert repo.exists(MachineId(value=_MACHINE_ID)) is True

    def test_exists_returns_false(self) -> None:
        repo, _ = _make_repo()
        assert repo.exists(MachineId(value="nope")) is False

    def test_exists_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.exists.side_effect = RuntimeError("boom")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: boom"):
            repo.exists(MachineId(value=_MACHINE_ID))


# ---------------------------------------------------------------------------
# count_by_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineRepositoryCountByStatus:
    def test_delegates_to_count_by_column_when_available(self) -> None:
        storage = MagicMock()
        storage.count_by_column = MagicMock(return_value={"running": 3, "pending": 1})
        repo, _ = _make_repo(storage)
        result = repo.count_by_status()
        assert result == {"running": 3, "pending": 1}
        storage.count_by_column.assert_called_once_with("status")

    def test_falls_back_to_slow_path_when_count_by_column_absent(self) -> None:
        storage = MagicMock(
            spec=["find_by_id", "find_by_criteria", "find_all", "delete", "exists", "save"]
        )
        storage.find_all.return_value = {_MACHINE_ID: _good_row(status="running")}
        repo = MachineRepositoryImpl(storage)
        result = repo.count_by_status()
        assert "running" in result

    def test_falls_back_when_count_by_column_returns_empty(self) -> None:
        storage = MagicMock()
        storage.count_by_column = MagicMock(return_value={})  # empty → fall back
        storage.find_all.return_value = {}
        repo, _ = _make_repo(storage)
        result = repo.count_by_status()
        assert isinstance(result, dict)
