"""Records written by one JSON strategy instance or process survive another's save."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from orb.infrastructure.storage.json.strategy import JSONStorageStrategy


def _strategy(path: Path, entity_type: str = "entities") -> JSONStorageStrategy:
    return JSONStorageStrategy(file_path=str(path), entity_type=entity_type, backup_enabled=False)


@pytest.mark.unit
class TestLostUpdate:
    def test_save_after_another_instance_wrote_keeps_both_records(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        a, b = _strategy(path), _strategy(path)

        a.find_by_id("missing")  # primes a's read cache with the empty section
        b.save("y", {"v": "y"})
        a.save("x", {"v": "x"})

        stored = json.loads(path.read_text())["entities"]
        assert set(stored) == {"x", "y"}

    def test_save_after_own_earlier_write_keeps_other_records(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        a, b = _strategy(path), _strategy(path)
        a.save("seed", {"v": 0})

        b.save("y", {"v": "y"})
        a.save("x", {"v": "x"})

        assert set(json.loads(path.read_text())["entities"]) == {"seed", "x", "y"}

    def test_batch_and_delete_apply_to_the_current_file(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        a, b = _strategy(path), _strategy(path)
        a.save("a1", {"v": 1})
        a.find_all()
        b.save("b1", {"v": 2})

        a.save_batch({"a2": {"v": 3}})
        a.delete_batch(["b1"])
        b.save("b2", {"v": 4})
        a.delete("a1")

        assert set(json.loads(path.read_text())["entities"]) == {"a2", "b2"}

    def test_threads_on_separate_instances_lose_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        strategies = [_strategy(path) for _ in range(4)]

        def work(index: int) -> None:
            for n in range(10):
                strategies[index].save(f"s{index}-{n}", {"n": n})

        threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(json.loads(path.read_text())["entities"]) == 40


@pytest.mark.unit
class TestReadCache:
    def test_read_sees_a_record_written_by_another_instance(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        a, b = _strategy(path), _strategy(path)
        a.save("one", {"v": 1})
        assert a.find_by_id("two") is None

        b.save("two", {"v": 2})

        assert a.find_by_id("two") == {"v": 2}
        assert a.exists("two")
        assert a.count() == 2

    def test_unchanged_file_is_served_from_the_cache(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        strategy = _strategy(path)
        strategy.save("one", {"v": 1})
        strategy.find_all()

        reads: list[int] = []
        original = strategy.file_manager.read_file_with_signature
        strategy.file_manager.read_file_with_signature = lambda: (  # type: ignore[method-assign]
            reads.append(1),
            original(),
        )[1]
        strategy.find_all()
        strategy.find_by_id("one")

        assert reads == []

    def test_signature_is_taken_from_the_descriptor_that_was_read(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        strategy = _strategy(path)
        strategy.save("one", {"v": 1})

        content, signature = strategy.file_manager.read_file_with_signature()

        assert json.loads(content)["entities"]["one"] == {"v": 1}
        assert signature == strategy.file_manager.current_signature()

    def test_missing_file_reads_as_empty_and_picks_up_creation(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        a, b = _strategy(path), _strategy(path)
        assert a.find_all() == {}

        b.save("one", {"v": 1})

        assert a.find_all() == {"one": {"v": 1}}

    def test_failed_write_leaves_the_cache_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "data.json"
        strategy = _strategy(path)
        strategy.save("one", {"v": 1})
        before = strategy.find_all()

        def boom(_content: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(strategy.file_manager, "write_file", boom)
        from orb.infrastructure.storage.exceptions import StorageError

        with pytest.raises(StorageError):
            strategy.save("two", {"v": 2})
        monkeypatch.undo()

        assert before == {"one": {"v": 1}}
        assert strategy.find_all() == {"one": {"v": 1}}
        assert json.loads(path.read_text())["entities"] == {"one": {"v": 1}}


_WRITER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from orb.infrastructure.storage.json.strategy import JSONStorageStrategy

    path, prefix, count, barrier = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
    entity_type = sys.argv[5]
    strategy = JSONStorageStrategy(file_path=path, entity_type=entity_type, backup_enabled=False)
    strategy.find_all()  # warm the cache like a long-lived process
    deadline = time.monotonic() + 30
    while not Path(barrier).exists():
        if time.monotonic() > deadline:
            raise SystemExit("barrier timeout")
        time.sleep(0.005)
    for n in range(count):
        strategy.save(f"{prefix}-{n}", {"n": n})
    """
)


@pytest.mark.unit
@pytest.mark.parametrize("single_file", [False, True], ids=["one-type", "three-types-one-file"])
def test_concurrent_processes_lose_no_record(tmp_path: Path, single_file: bool) -> None:
    path = tmp_path / "data.json"
    barrier = tmp_path / "go"
    script = tmp_path / "writer.py"
    script.write_text(_WRITER)
    types = ["machines", "requests", "templates"] if single_file else ["entities"]
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-I",
                str(script),
                str(path),
                f"p{i}",
                "15",
                str(barrier),
                types[i % len(types)],
            ],
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(6)
    ]
    barrier.touch()
    for proc in procs:
        _, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err

    stored = json.loads(path.read_text())
    ids = {entity_id for section in stored.values() for entity_id in section}
    assert ids == {f"p{i}-{n}" for i in range(6) for n in range(15)}


@pytest.mark.unit
def test_single_file_mode_saves_all_entity_types_without_deadlock(tmp_path: Path) -> None:
    from orb.infrastructure.storage.json.unit_of_work import JSONUnitOfWork

    uow = JSONUnitOfWork(
        data_dir=str(tmp_path),
        machine_file="all.json",
        request_file="all.json",
        template_file="all.json",
        backup_enabled=False,
    )
    errors: list[Exception] = []

    def work() -> None:
        try:
            with uow:
                uow.machines.storage_strategy.save("m1", {"v": 1})
                uow.requests.storage_strategy.save("r1", {"v": 2})
                uow.templates.storage_strategy.save("t1", {"v": 3})
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout=20)

    assert not worker.is_alive(), "single-file unit of work deadlocked"
    assert not errors
    stored = json.loads((tmp_path / "all.json").read_text())
    assert set(stored) == {"machines", "requests", "templates"}
