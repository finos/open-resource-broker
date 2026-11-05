"""JSON storage: version-checked saves and a unit of work with real rollback."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType
from orb.infrastructure.storage.exceptions import StorageError
from orb.infrastructure.storage.json.strategy import JSONStorageStrategy
from orb.infrastructure.storage.json.unit_of_work import JSONUnitOfWork


def _strategy(path: Path, entity_type: str = "entities") -> JSONStorageStrategy:
    return JSONStorageStrategy(file_path=str(path), entity_type=entity_type, backup_enabled=False)


def _stored(path: Path, entity_type: str = "entities") -> dict[str, Any]:
    return json.loads(path.read_text()).get(entity_type, {})


def _uow(tmp_path: Path, single_file: bool = False) -> JSONUnitOfWork:
    names = (
        {"machine_file": "all.json", "request_file": "all.json", "template_file": "all.json"}
        if single_file
        else {}
    )
    return JSONUnitOfWork(data_dir=str(tmp_path), backup_enabled=False, **names)


def _new_request() -> Request:
    return Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tmpl-1",
        machine_count=1,
        provider_type="aws",
        provider_name="aws-default",
        provider_api="RunInstances",
    )


@pytest.mark.unit
class TestSaveVersioned:
    def test_matching_baseline_writes_a_version_above_it(self, tmp_path: Path) -> None:
        strategy = _strategy(tmp_path / "d.json")
        strategy.save("a", {"version": 3, "v": "old"})

        written = strategy.save_versioned("a", {"version": 0, "v": "new"}, 3)

        assert written == 4
        assert _stored(tmp_path / "d.json")["a"] == {"version": 4, "v": "new"}

    def test_entity_counter_ahead_of_the_baseline_is_kept(self, tmp_path: Path) -> None:
        strategy = _strategy(tmp_path / "d.json")
        strategy.save("a", {"version": 1})

        assert strategy.save_versioned("a", {"version": 5}, 1) == 5

    def test_stale_baseline_raises_and_leaves_the_file_untouched(self, tmp_path: Path) -> None:
        path = tmp_path / "d.json"
        strategy = _strategy(path)
        strategy.save("a", {"version": 2, "v": "theirs"})
        before = path.read_bytes()

        with pytest.raises(ConcurrencyError) as exc_info:
            strategy.save_versioned("a", {"version": 1, "v": "mine"}, 1)

        assert "a" in str(exc_info.value)
        assert path.read_bytes() == before

    def test_deleted_record_with_a_baseline_is_a_conflict(self, tmp_path: Path) -> None:
        strategy = _strategy(tmp_path / "d.json")

        with pytest.raises(ConcurrencyError):
            strategy.save_versioned("gone", {"version": 1}, 1)

    def test_baseline_less_save_is_an_unconditional_upsert(self, tmp_path: Path) -> None:
        strategy = _strategy(tmp_path / "d.json")
        strategy.save("a", {"version": 7, "v": "theirs"})

        written = strategy.save_versioned("a", {"version": 0, "v": "mine"}, None)

        assert written == 0
        assert _stored(tmp_path / "d.json")["a"]["v"] == "mine"

    def test_record_without_a_version_counts_as_version_zero(self, tmp_path: Path) -> None:
        strategy = _strategy(tmp_path / "d.json")
        strategy.save("a", {"v": "legacy"})
        strategy.save("b", {"v": "null", "version": None})

        assert strategy.save_versioned("a", {"version": 0}, 0) == 1
        assert strategy.save_versioned("b", {"version": 0}, 0) == 1

    def test_stale_reload_after_a_conflict_sees_the_winner(self, tmp_path: Path) -> None:
        path = tmp_path / "d.json"
        loser, winner = _strategy(path), _strategy(path)
        loser.save("a", {"version": 1, "v": "base"})
        loser.find_by_id("a")  # the loser's cache holds version 1
        winner.save_versioned("a", {"version": 1, "v": "win"}, 1)

        with pytest.raises(ConcurrencyError):
            loser.save_versioned("a", {"version": 1, "v": "lose"}, 1)

        reloaded = loser.find_by_id("a")
        assert reloaded is not None and reloaded["v"] == "win"
        assert loser.save_versioned("a", {"version": 2, "v": "retry"}, reloaded["version"]) == 3

    def test_conflicting_save_drops_the_cache_instead_of_keeping_a_stale_copy(
        self, tmp_path: Path
    ) -> None:
        strategy = _strategy(tmp_path / "d.json")
        strategy.save("a", {"version": 1})
        with pytest.raises(ConcurrencyError):
            strategy.save_versioned("a", {"version": 1}, 0)

        assert strategy._cache_valid is False

    def test_batch_with_one_conflict_writes_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "d.json"
        strategy = _strategy(path)
        strategy.save("a", {"version": 1})
        strategy.save("b", {"version": 5})
        before = path.read_bytes()

        with pytest.raises(ConcurrencyError):
            strategy.save_versioned_batch(
                [("a", {"version": 1, "v": "x"}, 1), ("b", {"version": 5, "v": "y"}, 4)]
            )

        assert path.read_bytes() == before

    def test_batch_is_one_file_write(self, tmp_path: Path) -> None:
        strategy = _strategy(tmp_path / "d.json")
        strategy.save("a", {"version": 1})
        with patch.object(
            strategy.file_manager, "write_file", wraps=strategy.file_manager.write_file
        ) as write:
            versions = strategy.save_versioned_batch(
                [("a", {"version": 1}, 1), ("b", {"version": 0}, None), ("c", {"version": 0}, None)]
            )

        assert versions == [2, 0, 0]
        assert write.call_count == 1

    def test_plain_save_stays_unconditional(self, tmp_path: Path) -> None:
        strategy = _strategy(tmp_path / "d.json")
        strategy.save("a", {"version": 9})
        strategy.save("a", {"version": 1})

        assert _stored(tmp_path / "d.json")["a"]["version"] == 1


@pytest.mark.unit
class TestUnitOfWorkRollback:
    def test_rollback_leaves_the_file_bytes_identical(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        uow.requests.storage_strategy.save("r1", {"version": 1})
        path = tmp_path / "requests.json"
        before = path.read_bytes()

        uow.begin()
        uow.requests.storage_strategy.save("r2", {"version": 1})
        uow.requests.storage_strategy.delete("r1")
        uow.requests.storage_strategy.save_versioned("r1", {"version": 1}, 1)
        uow.rollback()

        assert path.read_bytes() == before
        assert not (tmp_path / "machines.json").exists()

    def test_exception_in_the_block_writes_nothing(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)

        def fail() -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            with uow:
                uow.machines.storage_strategy.save("m1", {"version": 1})
                fail()

        assert not (tmp_path / "machines.json").exists()

    def test_buffered_writes_are_invisible_to_other_readers_until_commit(
        self, tmp_path: Path
    ) -> None:
        uow = _uow(tmp_path)
        other = _strategy(tmp_path / "requests.json", "requests")

        uow.begin()
        uow.requests.storage_strategy.save("r1", {"version": 1})
        assert other.find_by_id("r1") is None
        uow.commit()

        assert other.find_by_id("r1") == {"version": 1}

    def test_holder_does_not_move_on_rollback_and_moves_on_commit(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        request = _new_request()
        uow.begin()
        uow.requests.save(request)
        assert request.persisted_revision.value is None
        uow.rollback()
        assert request.persisted_revision.value is None

        with uow:
            uow.requests.save(request)
            assert request.persisted_revision.value is None
        assert request.persisted_revision.value == request.version

    def test_holder_does_not_move_when_the_commit_conflicts(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        request = _new_request()
        with uow:
            uow.requests.save(request)
        mine = uow.requests.find_by_id(str(request.request_id))
        theirs = _uow(tmp_path).requests.find_by_id(str(request.request_id))
        assert mine is not None and theirs is not None
        with _uow(tmp_path) as other:
            other.requests.save(theirs.start_processing())
        baseline = mine.persisted_revision.value

        with pytest.raises(ConcurrencyError):
            with uow:
                uow.requests.save(mine.start_processing())

        assert mine.persisted_revision.value == baseline


@pytest.mark.unit
class TestUnitOfWorkCommit:
    def test_conflict_on_the_second_buffered_write_writes_nothing(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        strategy = uow.requests.storage_strategy
        strategy.save("a", {"version": 1})
        strategy.save("b", {"version": 1})
        strategy.save("b", {"version": 2})  # b moved on after the unit of work read it
        path = tmp_path / "requests.json"
        before = path.read_bytes()

        with pytest.raises(ConcurrencyError):
            with uow:
                strategy.save_versioned("a", {"version": 1, "v": "x"}, 1)
                strategy.save_versioned("b", {"version": 1, "v": "y"}, 1)

        assert path.read_bytes() == before

    def test_conflict_in_the_second_file_leaves_the_first_unchanged(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        uow.requests.storage_strategy.save("r1", {"version": 1})
        uow.machines.storage_strategy.save("m1", {"version": 1})
        uow.machines.storage_strategy.save("m1", {"version": 2})
        before = {p.name: p.read_bytes() for p in tmp_path.glob("*.json")}

        with pytest.raises(ConcurrencyError):
            with uow:
                uow.requests.storage_strategy.save_versioned("r1", {"version": 1}, 1)
                uow.machines.storage_strategy.save_versioned("m1", {"version": 1}, 1)

        assert {p.name: p.read_bytes() for p in tmp_path.glob("*.json")} == before

    def test_commit_writes_every_file_once(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        written: list[str] = []
        for strategy in (uow.requests, uow.machines, uow.templates):
            fm = strategy.storage_strategy.file_manager
            real = fm.write_file
            fm.write_file = lambda content, real=real, name=fm.file_path.name: (  # type: ignore[method-assign]
                written.append(name),
                real(content),
            )[1]

        with uow:
            uow.requests.storage_strategy.save("r1", {"version": 1})
            uow.machines.storage_strategy.save("m1", {"version": 1})
            uow.machines.storage_strategy.save("m2", {"version": 1})

        assert sorted(written) == ["machines.json", "requests.json"]

    def test_requests_file_is_written_first_for_puts_and_last_for_deletes(
        self, tmp_path: Path
    ) -> None:
        uow = _uow(tmp_path)
        uow.requests.storage_strategy.save("r1", {"version": 1})
        uow.machines.storage_strategy.save("m1", {"version": 1})
        order: list[str] = []
        for strategy in (uow.requests, uow.machines):
            fm = strategy.storage_strategy.file_manager
            real = fm.write_file
            fm.write_file = lambda content, real=real, name=fm.file_path.name: (  # type: ignore[method-assign]
                order.append(name),
                real(content),
            )[1]

        with uow:
            uow.requests.storage_strategy.save("r2", {"version": 1})
            uow.machines.storage_strategy.save("m2", {"version": 1})
        assert order == ["requests.json", "machines.json"]

        order.clear()
        with uow:
            uow.machines.storage_strategy.delete("m2")
            uow.requests.storage_strategy.delete("r2")
        assert order == ["machines.json", "requests.json"]

    def test_single_file_mode_commits_with_one_write_and_does_not_hang(
        self, tmp_path: Path
    ) -> None:
        uow = _uow(tmp_path, single_file=True)
        writes: list[int] = []
        for strategy in (uow.requests, uow.machines, uow.templates):
            fm = strategy.storage_strategy.file_manager
            real = fm.write_file
            fm.write_file = lambda content, real=real: (writes.append(1), real(content))[1]  # type: ignore[method-assign]
        errors: list[Exception] = []

        def work() -> None:
            try:
                with uow:
                    uow.requests.storage_strategy.save("r1", {"version": 1})
                    uow.machines.storage_strategy.save("m1", {"version": 1})
                    uow.templates.storage_strategy.save("t1", {"version": 1})
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(timeout=20)

        assert not thread.is_alive()
        assert not errors
        assert writes == [1]
        assert set(json.loads((tmp_path / "all.json").read_text())) == {
            "requests",
            "machines",
            "templates",
        }

    def test_failure_writing_the_second_file_restores_the_first(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        uow.requests.storage_strategy.save("r1", {"version": 1, "v": "old"})
        uow.machines.storage_strategy.save("m1", {"version": 1, "v": "old"})
        before = {p.name: p.read_bytes() for p in tmp_path.glob("*.json")}
        machines_fm = uow.machines.storage_strategy.file_manager
        real = machines_fm.write_file

        def failing(content: str) -> None:
            if "new" in content:
                raise OSError("disk full")
            real(content)

        machines_fm.write_file = failing  # type: ignore[method-assign]

        with pytest.raises(StorageError):
            with uow:
                uow.requests.storage_strategy.save("r1", {"version": 2, "v": "new"})
                uow.machines.storage_strategy.save("m1", {"version": 2, "v": "new"})

        assert {p.name: p.read_bytes() for p in tmp_path.glob("*.json")} == before

    def test_failure_writing_the_second_file_removes_a_file_that_did_not_exist(
        self, tmp_path: Path
    ) -> None:
        uow = _uow(tmp_path)
        machines_fm = uow.machines.storage_strategy.file_manager

        def failing(_content: str) -> None:
            raise OSError("disk full")

        machines_fm.write_file = failing  # type: ignore[method-assign]

        with pytest.raises(StorageError):
            with uow:
                uow.requests.storage_strategy.save("r1", {"version": 1})
                uow.machines.storage_strategy.save("m1", {"version": 1})

        assert not (tmp_path / "requests.json").exists()

    def test_two_lineages_of_one_record_are_a_conflict_at_commit(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        strategy = uow.requests.storage_strategy
        strategy.save("a", {"version": 2})

        with pytest.raises(ConcurrencyError):
            with uow:
                strategy.save_versioned("a", {"version": 1}, 1)
                strategy.save_versioned("a", {"version": 1}, 2)

        assert _stored(tmp_path / "requests.json", "requests")["a"] == {"version": 2}

    def test_same_baseline_saved_twice_keeps_the_last_payload(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        strategy = uow.requests.storage_strategy
        strategy.save("a", {"version": 1})

        with uow:
            strategy.save_versioned("a", {"version": 1, "v": "first"}, 1)
            strategy.save_versioned("a", {"version": 2, "v": "second"}, 1)

        assert _stored(tmp_path / "requests.json", "requests")["a"] == {"version": 2, "v": "second"}

    def test_post_commit_callbacks_run_once_after_all_files_are_written(
        self, tmp_path: Path
    ) -> None:
        uow = _uow(tmp_path)
        seen: list[bool] = []
        with uow:
            uow.requests.storage_strategy.save("r1", {"version": 1})
            uow.machines.storage_strategy.save("m1", {"version": 1})
            for strategy in (uow.requests, uow.machines, uow.templates):
                strategy.storage_strategy.defer_until_commit(
                    lambda: seen.append((tmp_path / "requests.json").exists())
                )
            assert seen == []

        assert seen == [True, True, True]

    def test_post_commit_callbacks_do_not_run_on_rollback_or_failure(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        uow.requests.storage_strategy.save("a", {"version": 2})
        ran: list[int] = []

        uow.begin()
        uow.requests.storage_strategy.defer_until_commit(lambda: ran.append(1))
        uow.rollback()
        with pytest.raises(ConcurrencyError):
            with uow:
                uow.requests.storage_strategy.save_versioned("a", {"version": 1}, 1)
                uow.requests.storage_strategy.defer_until_commit(lambda: ran.append(2))

        assert ran == []

    def test_callback_runs_immediately_outside_a_transaction(self, tmp_path: Path) -> None:
        ran: list[int] = []
        _strategy(tmp_path / "d.json").defer_until_commit(lambda: ran.append(1))

        assert ran == [1]


@pytest.mark.unit
class TestOverlayReads:
    def test_reads_inside_the_unit_of_work_see_its_own_writes(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        strategy = uow.requests.storage_strategy
        strategy.save("keep", {"version": 1, "status": "a"})
        strategy.save("drop", {"version": 1, "status": "a"})

        with uow:
            strategy.save("new", {"version": 1, "status": "b"})
            strategy.delete("drop")
            strategy.save("keep", {"version": 2, "status": "b"})

            assert strategy.find_by_id("new") == {"version": 1, "status": "b"}
            assert strategy.find_by_id("drop") is None
            assert strategy.exists("new") and not strategy.exists("drop")
            assert strategy.find_by_id("keep")["version"] == 2  # type: ignore[index]
            assert set(strategy.find_all()) == {"keep", "new"}
            assert strategy.count() == 2
            assert len(strategy.find_by_criteria({"status": "b"})) == 2

    def test_point_lookups_do_not_copy_the_stored_section(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        strategy = uow.requests.storage_strategy
        strategy.save_batch({str(i): {"version": 1} for i in range(50)})

        class Untouchable(dict):  # type: ignore[type-arg]
            def copy(self) -> Any:
                raise AssertionError("section copied")

            def items(self) -> Any:
                raise AssertionError("section iterated")

            def __iter__(self) -> Any:
                raise AssertionError("section iterated")

            def keys(self) -> Any:
                raise AssertionError("section iterated")

            def values(self) -> Any:
                raise AssertionError("section iterated")

        base = Untouchable(strategy.find_all())
        with uow:
            strategy.save("new", {"version": 1})
            with patch.object(strategy, "_load_data", return_value=base):
                assert strategy.find_by_id("new") is not None
                assert strategy.find_by_id("7") == {"version": 1}
                assert strategy.exists("7")
                assert strategy.find_by_id("nope") is None


@pytest.mark.unit
class TestLocking:
    def test_lock_wait_times_out_with_a_storage_error(self, tmp_path: Path) -> None:
        strategy = _strategy(tmp_path / "d.json")
        holder = _strategy(tmp_path / "d.json")

        with holder.file_manager.exclusive_write_lock():
            with pytest.raises(StorageError):
                with strategy.file_manager.exclusive_write_lock(timeout=0.2):
                    pass

    def test_symlinked_alias_shares_the_lock(self, tmp_path: Path) -> None:
        real = tmp_path / "real.json"
        real.write_text("{}")
        alias = tmp_path / "alias.json"
        os.symlink(real, alias)
        via_real, via_alias = _strategy(real), _strategy(alias)

        assert via_real.file_manager.lock_path == via_alias.file_manager.lock_path
        with via_real.file_manager.exclusive_write_lock():
            with pytest.raises(StorageError):
                with via_alias.file_manager.exclusive_write_lock(timeout=0.2):
                    pass

    def test_lock_is_free_again_after_a_timeout(self, tmp_path: Path) -> None:
        a, b = _strategy(tmp_path / "d.json"), _strategy(tmp_path / "d.json")
        with a.file_manager.exclusive_write_lock():
            with pytest.raises(StorageError):
                with b.file_manager.exclusive_write_lock(timeout=0.1):
                    pass

        with b.file_manager.exclusive_write_lock(timeout=1):
            pass


@pytest.mark.unit
class TestBaselineAcrossBufferedOperations:
    def test_versioned_put_after_a_delete_keeps_its_baseline_check(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        strategy = uow.requests.storage_strategy
        strategy.save("a", {"version": 3})

        with pytest.raises(ConcurrencyError):
            with uow:
                strategy.delete("a")
                strategy.save_versioned("a", {"version": 1}, 1)  # stored version is 3

        assert _stored(tmp_path / "requests.json", "requests")["a"] == {"version": 3}

    def test_versioned_put_after_a_baseline_less_put_keeps_its_baseline_check(
        self, tmp_path: Path
    ) -> None:
        uow = _uow(tmp_path)
        strategy = uow.requests.storage_strategy
        strategy.save("a", {"version": 3})

        with pytest.raises(ConcurrencyError):
            with uow:
                strategy.save("a", {"version": 9})
                strategy.save_versioned("a", {"version": 1}, 1)

    def test_matching_baseline_after_a_delete_commits(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        strategy = uow.requests.storage_strategy
        strategy.save("a", {"version": 1})

        with uow:
            strategy.delete("a")
            strategy.save_versioned("a", {"version": 1, "v": "again"}, 1)

        assert _stored(tmp_path / "requests.json", "requests")["a"]["v"] == "again"

    def test_commit_conflict_drops_every_strategys_read_cache(self, tmp_path: Path) -> None:
        uow = _uow(tmp_path)
        uow.requests.storage_strategy.save("r", {"version": 2})
        uow.machines.storage_strategy.save("m", {"version": 1})
        for repository in (uow.requests, uow.machines):
            repository.storage_strategy.find_all()
            assert repository.storage_strategy._cache_valid is True

        with pytest.raises(ConcurrencyError):
            with uow:
                uow.requests.storage_strategy.save_versioned("r", {"version": 1}, 1)
                uow.machines.storage_strategy.save("m", {"version": 2})

        assert uow.requests.storage_strategy._cache_valid is False
        assert uow.machines.storage_strategy._cache_valid is False
