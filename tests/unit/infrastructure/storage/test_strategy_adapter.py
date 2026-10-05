"""Unit tests for StorageStrategyAdapter."""

from unittest.mock import MagicMock

import pytest

from orb.infrastructure.storage.adapters.strategy_adapter import StorageStrategyAdapter

pytestmark = pytest.mark.unit


def _make_adapter() -> tuple[StorageStrategyAdapter, MagicMock]:
    strategy = MagicMock()
    adapter = StorageStrategyAdapter(storage_strategy=strategy)
    return adapter, strategy


class TestStorageReaderInterface:
    def test_find_by_id_delegates(self):
        adapter, strategy = _make_adapter()
        strategy.find_by_id.return_value = {"id": "1"}

        assert adapter.find_by_id("1") == {"id": "1"}
        strategy.find_by_id.assert_called_once_with("1")

    def test_find_all_delegates(self):
        adapter, strategy = _make_adapter()
        strategy.find_all.return_value = [{"id": "1"}]

        assert adapter.find_all() == [{"id": "1"}]
        strategy.find_all.assert_called_once_with()

    def test_exists_delegates(self):
        adapter, strategy = _make_adapter()
        strategy.exists.return_value = True

        assert adapter.exists("1") is True
        strategy.exists.assert_called_once_with("1")

    def test_find_by_criteria_delegates(self):
        adapter, strategy = _make_adapter()
        strategy.find_by_criteria.return_value = [{"id": "1"}]

        result = adapter.find_by_criteria({"status": "active"})

        assert result == [{"id": "1"}]
        strategy.find_by_criteria.assert_called_once_with({"status": "active"})


class TestStorageWriterInterface:
    def test_save_delegates(self):
        adapter, strategy = _make_adapter()

        adapter.save("1", {"name": "x"})

        strategy.save.assert_called_once_with("1", {"name": "x"})

    def test_delete_delegates(self):
        adapter, strategy = _make_adapter()

        adapter.delete("1")

        strategy.delete.assert_called_once_with("1")


class TestBatchStorageInterface:
    def test_save_batch_delegates(self):
        adapter, strategy = _make_adapter()
        entities = {"1": {"name": "x"}, "2": {"name": "y"}}

        adapter.save_batch(entities)

        strategy.save_batch.assert_called_once_with(entities)

    def test_delete_batch_delegates(self):
        adapter, strategy = _make_adapter()

        adapter.delete_batch(["1", "2"])

        strategy.delete_batch.assert_called_once_with(["1", "2"])


class TestTransactionalStorageInterface:
    def test_begin_transaction_delegates(self):
        adapter, strategy = _make_adapter()

        adapter.begin_transaction()

        strategy.begin_transaction.assert_called_once_with()

    def test_commit_transaction_delegates(self):
        adapter, strategy = _make_adapter()

        adapter.commit_transaction()

        strategy.commit_transaction.assert_called_once_with()

    def test_rollback_transaction_delegates(self):
        adapter, strategy = _make_adapter()

        adapter.rollback_transaction()

        strategy.rollback_transaction.assert_called_once_with()
