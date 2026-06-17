"""Contract tests applied to every storage backend.

Each backend (JSON, SQL/SQLite, DynamoDB/moto) must satisfy the same CRUD
behaviour. Fixtures live in conftest.py.
"""

import pytest


@pytest.mark.integration
class TestStorageStrategyCRUD:
    def test_save_and_find(self, storage_strategy):
        storage_strategy.save("e1", {"id": "e1", "name": "alpha"})
        loaded = storage_strategy.find_by_id("e1")
        assert loaded is not None
        assert loaded.get("id") == "e1"

    def test_find_missing_returns_none(self, storage_strategy):
        assert storage_strategy.find_by_id("nope") is None

    def test_find_all_returns_inserted(self, storage_strategy):
        storage_strategy.save("a", {"id": "a", "name": "alpha"})
        storage_strategy.save("b", {"id": "b", "name": "beta"})
        all_rows = storage_strategy.find_all()
        assert "a" in all_rows
        assert "b" in all_rows

    def test_delete_removes_entity(self, storage_strategy):
        storage_strategy.save("d1", {"id": "d1", "name": "doomed"})
        storage_strategy.delete("d1")
        assert storage_strategy.find_by_id("d1") is None

    def test_save_overwrites_existing(self, storage_strategy):
        storage_strategy.save("u1", {"id": "u1", "name": "before"})
        storage_strategy.save("u1", {"id": "u1", "name": "after"})
        loaded = storage_strategy.find_by_id("u1")
        assert loaded is not None
        assert loaded.get("name") == "after"


@pytest.mark.integration
class TestStorageStrategyBatch:
    def test_save_batch(self, storage_strategy):
        batch = {
            "x": {"id": "x", "name": "x-name"},
            "y": {"id": "y", "name": "y-name"},
        }
        storage_strategy.save_batch(batch)
        assert storage_strategy.find_by_id("x") is not None
        assert storage_strategy.find_by_id("y") is not None

    def test_delete_batch(self, storage_strategy):
        storage_strategy.save("p", {"id": "p", "name": "p"})
        storage_strategy.save("q", {"id": "q", "name": "q"})
        storage_strategy.delete_batch(["p", "q"])
        assert storage_strategy.find_by_id("p") is None
        assert storage_strategy.find_by_id("q") is None
