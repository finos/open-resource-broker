"""Unit tests for JSONStorageStrategy covering additional branches."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orb.infrastructure.storage.json.strategy import JSONStorageStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy(tmp_path: Path, entity_type: str = "entities") -> JSONStorageStrategy:
    return JSONStorageStrategy(
        file_path=str(tmp_path / "data.json"),
        entity_type=entity_type,
        backup_enabled=False,
    )


# ---------------------------------------------------------------------------
# save / find_by_id roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSaveFindById:
    def test_save_and_find_roundtrip(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"name": "Alice", "score": 5})
        result = strategy.find_by_id("e1")
        assert result is not None
        assert result["name"] == "Alice"

    def test_find_nonexistent_returns_none(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        assert strategy.find_by_id("ghost") is None

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"name": "old"})
        strategy.save("e1", {"name": "new"})
        result = strategy.find_by_id("e1")
        assert result is not None
        assert result["name"] == "new"


# ---------------------------------------------------------------------------
# find_all
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindAll:
    def test_find_all_returns_all_saved_entities(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("a", {"v": 1})
        strategy.save("b", {"v": 2})
        result = strategy.find_all()
        assert "a" in result
        assert "b" in result

    def test_find_all_returns_copy_not_reference(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"v": 1})
        r1 = strategy.find_all()
        r1["mutated"] = {}
        r2 = strategy.find_all()
        assert "mutated" not in r2

    def test_find_all_on_empty_file_returns_empty(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        assert strategy.find_all() == {}


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDelete:
    def test_delete_removes_entity(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"v": 1})
        strategy.delete("e1")
        assert strategy.find_by_id("e1") is None

    def test_delete_nonexistent_entity_no_error(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.delete("does_not_exist")  # must not raise

    def test_delete_does_not_affect_other_entities(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("a", {"v": 1})
        strategy.save("b", {"v": 2})
        strategy.delete("a")
        assert strategy.find_by_id("b") is not None


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExists:
    def test_exists_returns_true_for_saved_entity(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"v": 1})
        assert strategy.exists("e1") is True

    def test_exists_returns_false_for_missing_entity(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        assert strategy.exists("nope") is False


# ---------------------------------------------------------------------------
# save_batch / delete_batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBatchOperations:
    def test_save_batch_persists_all(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save_batch({"x": {"v": 1}, "y": {"v": 2}})
        assert strategy.find_by_id("x") is not None
        assert strategy.find_by_id("y") is not None

    def test_delete_batch_removes_all(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save_batch({"a": {"v": 1}, "b": {"v": 2}})
        strategy.delete_batch(["a", "b"])
        assert strategy.find_by_id("a") is None
        assert strategy.find_by_id("b") is None

    def test_delete_batch_ignores_nonexistent_ids(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("c", {"v": 1})
        strategy.delete_batch(["c", "ghost"])
        assert strategy.find_by_id("c") is None


# ---------------------------------------------------------------------------
# count
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCount:
    def test_count_reflects_number_of_entities(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        assert strategy.count() == 0
        strategy.save("e1", {"v": 1})
        strategy.save("e2", {"v": 2})
        assert strategy.count() == 2

    def test_count_after_delete(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"v": 1})
        strategy.delete("e1")
        assert strategy.count() == 0


# ---------------------------------------------------------------------------
# find_by_criteria
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindByCriteria:
    def test_equality_filter(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"status": "running"})
        strategy.save("e2", {"status": "stopped"})
        result = strategy.find_by_criteria({"status": "running"})
        assert len(result) == 1
        assert result[0]["status"] == "running"

    def test_in_operator(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"status": "a"})
        strategy.save("e2", {"status": "b"})
        strategy.save("e3", {"status": "c"})
        result = strategy.find_by_criteria({"status": {"$in": ["a", "c"]}})
        statuses = {r["status"] for r in result}
        assert statuses == {"a", "c"}

    def test_regex_operator(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"name": "Alice"})
        strategy.save("e2", {"name": "Bob"})
        result = strategy.find_by_criteria({"name": {"$regex": "^Al"}})
        assert len(result) == 1
        assert result[0]["name"] == "Alice"

    def test_no_match_returns_empty(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"status": "ok"})
        result = strategy.find_by_criteria({"status": "missing"})
        assert result == []

    def test_missing_key_in_entity_does_not_match(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"color": "red"})  # no 'status' field
        result = strategy.find_by_criteria({"status": "ok"})
        assert result == []


# ---------------------------------------------------------------------------
# transaction methods (delegated to MemoryTransactionManager)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTransactionDelegation:
    def test_begin_commit_transaction_no_error(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.begin_transaction()
        strategy.commit_transaction()

    def test_begin_rollback_transaction_no_error(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.begin_transaction()
        strategy.rollback_transaction()


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanup:
    def test_cleanup_invalidates_cache(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"v": 1})
        # warm cache
        strategy.find_all()
        assert strategy._cache_valid is True
        strategy.cleanup()
        assert strategy._cache_valid is False
        assert strategy._data_cache is None


# ---------------------------------------------------------------------------
# is_healthy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsHealthy:
    def test_healthy_when_file_absent_but_dir_exists(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        ok, details = strategy.is_healthy()
        assert ok is True
        assert details["state"] == "empty"

    def test_healthy_with_valid_json_file(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"name": "Alice"})
        ok, _ = strategy.is_healthy()
        assert ok is True

    def test_unhealthy_when_parent_dir_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent_dir" / "data.json"
        strategy = JSONStorageStrategy(
            file_path=str(missing),
            create_dirs=False,
            backup_enabled=False,
        )
        ok, details = strategy.is_healthy()
        assert ok is False
        assert "parent directory" in details["reason"]

    def test_unhealthy_when_file_is_not_json_object(self, tmp_path: Path) -> None:
        data_file = tmp_path / "data.json"
        data_file.write_text("[1, 2, 3]", encoding="utf-8")
        strategy = JSONStorageStrategy(file_path=str(data_file), backup_enabled=False)
        ok, details = strategy.is_healthy()
        assert ok is False
        assert "expected JSON object" in details["reason"]

    def test_unhealthy_when_record_is_not_dict(self, tmp_path: Path) -> None:
        # The health check reads the raw file and samples payload[first_key].
        # For a flat (non-hierarchical) file where a top-level key maps to a
        # non-dict value, the shape-sanity check must reject it.
        data_file = tmp_path / "data.json"
        # Write a flat JSON object whose first value is a primitive, not a dict.
        data_file.write_text(json.dumps({"e1": "not_a_dict"}), encoding="utf-8")
        strategy = JSONStorageStrategy(
            file_path=str(data_file), entity_type="entities", backup_enabled=False
        )
        ok, details = strategy.is_healthy()
        assert ok is False
        assert "expected dict" in details["reason"]


# ---------------------------------------------------------------------------
# multi-worker warning
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMultiWorkerWarning:
    def test_warning_emitted_for_multiple_workers(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {"WEB_CONCURRENCY": "4"}):
            with patch("orb.infrastructure.storage.json.strategy.get_logger") as mock_get_logger:
                mock_logger = MagicMock()
                mock_get_logger.return_value = mock_logger
                JSONStorageStrategy(
                    file_path=str(tmp_path / "d.json"),
                    backup_enabled=False,
                )
                # warning should have been called with the multi-worker message
                calls = [str(c) for c in mock_logger.warning.call_args_list]
                multi_worker_warned = any("workers" in c.lower() for c in calls)
                assert multi_worker_warned

    def test_no_warning_for_single_worker(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {"WEB_CONCURRENCY": "1"}):
            with patch("orb.infrastructure.storage.json.strategy.get_logger") as mock_get_logger:
                mock_logger = MagicMock()
                mock_get_logger.return_value = mock_logger
                JSONStorageStrategy(
                    file_path=str(tmp_path / "d2.json"),
                    backup_enabled=False,
                )
                calls = [str(c) for c in mock_logger.warning.call_args_list]
                multi_worker_warned = any("workers" in c.lower() for c in calls)
                assert not multi_worker_warned


# ---------------------------------------------------------------------------
# _load_data — cache and recovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadData:
    def test_cache_returned_when_valid(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path)
        strategy.save("e1", {"v": 1})
        # Mark cache valid and populate
        strategy._cache_valid = True
        strategy._data_cache = {"e1": {"v": 99}}
        # _load_data should return the in-memory cache without reading disk
        result = strategy._load_data()
        assert result["e1"]["v"] == 99

    def test_empty_file_returns_empty_dict(self, tmp_path: Path) -> None:
        data_file = tmp_path / "data.json"
        data_file.write_text("", encoding="utf-8")
        strategy = JSONStorageStrategy(
            file_path=str(data_file), entity_type="entities", backup_enabled=False
        )
        result = strategy._load_data()
        assert result == {}

    def test_non_dict_json_initialises_empty(self, tmp_path: Path) -> None:
        data_file = tmp_path / "data.json"
        data_file.write_text("[1, 2, 3]", encoding="utf-8")
        strategy = JSONStorageStrategy(
            file_path=str(data_file), entity_type="entities", backup_enabled=False
        )
        result = strategy._load_data()
        assert result == {}


# ---------------------------------------------------------------------------
# A storage file that does not parse
# ---------------------------------------------------------------------------

_CORRUPT = b'{"entities": {"e1": {"v": 1}'


def _backed_up_strategy(tmp_path: Path) -> JSONStorageStrategy:
    return JSONStorageStrategy(
        file_path=str(tmp_path / "data.json"),
        entity_type="entities",
        backup_enabled=True,
    )


def _write_backup(tmp_path: Path, name: str, content: bytes, age: int) -> Path:
    """A backup file ``age`` seconds old (larger is older)."""
    import os
    import time

    backup = tmp_path / "backups" / f"data.backup_{name}.json"
    backup.parent.mkdir(exist_ok=True)
    backup.write_bytes(content)
    stamp = time.time() - age
    os.utime(backup, (stamp, stamp))
    return backup


def _backup_listing(tmp_path: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted((tmp_path / "backups").iterdir())}


@pytest.mark.unit
class TestCorruptStorageFile:
    def test_no_backup_raises_and_leaves_the_file(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _backed_up_strategy(tmp_path)
        data_file = tmp_path / "data.json"
        data_file.write_bytes(_CORRUPT)

        with pytest.raises(StorageError, match="data.json"):
            strategy.find_all()

        assert data_file.read_bytes() == _CORRUPT
        assert not list(tmp_path.glob("data.json.corrupt-*"))

    def test_valid_backup_is_restored_and_the_damaged_file_kept(self, tmp_path: Path) -> None:
        strategy = _backed_up_strategy(tmp_path)
        data_file = tmp_path / "data.json"
        data_file.write_bytes(_CORRUPT)
        _write_backup(tmp_path, "1", json.dumps({"entities": {"b1": {"v": 2}}}).encode(), 10)

        assert strategy.find_all() == {"b1": {"v": 2}}

        assert json.loads(data_file.read_text())["entities"] == {"b1": {"v": 2}}
        (corrupt_copy,) = tmp_path.glob("data.json.corrupt-*")
        assert corrupt_copy.read_bytes() == _CORRUPT

    def test_corrupt_newest_backup_falls_back_to_an_older_one(self, tmp_path: Path) -> None:
        strategy = _backed_up_strategy(tmp_path)
        (tmp_path / "data.json").write_bytes(_CORRUPT)
        _write_backup(tmp_path, "new", b"{not json", 10)
        _write_backup(tmp_path, "old", json.dumps({"entities": {"old": {"v": 3}}}).encode(), 100)

        assert strategy.find_all() == {"old": {"v": 3}}

    def test_every_backup_corrupt_raises_without_looping(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _backed_up_strategy(tmp_path)
        data_file = tmp_path / "data.json"
        data_file.write_bytes(_CORRUPT)
        _write_backup(tmp_path, "a", b"{not json", 10)
        _write_backup(tmp_path, "b", b"\xff\xfe", 100)

        with pytest.raises(StorageError, match="no valid backup"):
            strategy.find_all()

        assert data_file.read_bytes() == _CORRUPT
        assert not list(tmp_path.glob("data.json.corrupt-*"))

    def test_read_error_on_a_valid_file_never_restores(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _backed_up_strategy(tmp_path)
        data_file = tmp_path / "data.json"
        good = json.dumps({"entities": {"live": {"v": 9}}}).encode()
        data_file.write_bytes(good)
        _write_backup(tmp_path, "1", json.dumps({"entities": {"stale": {"v": 1}}}).encode(), 10)

        with (
            patch.object(
                strategy.file_manager,
                "read_file_with_signature",
                side_effect=OSError(5, "Input/output error"),
            ),
            pytest.raises(StorageError, match="Input/output error"),
        ):
            strategy.find_all()

        assert data_file.read_bytes() == good
        assert not list(tmp_path.glob("data.json.corrupt-*"))

    def test_save_against_a_corrupt_file_writes_and_backs_up_nothing(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _backed_up_strategy(tmp_path)
        data_file = tmp_path / "data.json"
        data_file.write_bytes(_CORRUPT)
        _write_backup(tmp_path, "1", json.dumps({"entities": {"b1": {"v": 2}}}).encode(), 10)
        before = _backup_listing(tmp_path)

        with pytest.raises(StorageError):
            strategy.save("e2", {"v": 5})

        assert data_file.read_bytes() == _CORRUPT
        assert _backup_listing(tmp_path) == before

    def test_exists_and_count_raise_on_a_corrupt_file(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _backed_up_strategy(tmp_path)
        (tmp_path / "data.json").write_bytes(_CORRUPT)

        with pytest.raises(StorageError):
            strategy.exists("e1")
        with pytest.raises(StorageError):
            strategy.count()

    def test_unit_of_work_against_a_corrupt_file_backs_up_nothing(self, tmp_path: Path) -> None:
        strategy = _backed_up_strategy(tmp_path)
        data_file = tmp_path / "data.json"
        data_file.write_bytes(_CORRUPT)
        _write_backup(tmp_path, "1", json.dumps({"entities": {"b1": {"v": 2}}}).encode(), 10)
        before = _backup_listing(tmp_path)

        strategy.begin_transaction()
        strategy.save("e2", {"v": 5})
        with pytest.raises(ValueError):
            strategy.commit_transaction()

        assert data_file.read_bytes() == _CORRUPT
        assert _backup_listing(tmp_path) == before
