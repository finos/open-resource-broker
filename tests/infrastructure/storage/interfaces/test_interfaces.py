"""Tests for segregated storage interfaces."""

from typing import Any, Optional, Union
from unittest.mock import Mock

import pytest

from infrastructure.storage.adapters.strategy_adapter import StorageStrategyAdapter
from infrastructure.storage.base.strategy import BaseStorageStrategy, StorageStrategy
from infrastructure.storage.interfaces import (
    BatchStorage,
    StorageReader,
    StorageWriter,
    TransactionalStorage,
)


class MockStorageStrategy(BaseStorageStrategy):
    """Mock storage strategy for testing."""

    def __init__(self):
        super().__init__()
        self._data = {}

    def cleanup(self) -> None:
        """Clean up resources."""
        pass

    def __enter__(self) -> "StorageStrategy":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return False

    def save(self, entity_id: str, data: dict[str, Any]) -> None:
        """Save entity data."""
        self._data[entity_id] = data.copy()

    def find_by_id(self, entity_id: str) -> Optional[dict[str, Any]]:
        """Find entity by ID."""
        return self._data.get(entity_id)

    def find_all(self) -> Union[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Find all entities."""
        return self._data.copy()

    def delete(self, entity_id: str) -> None:
        """Delete entity."""
        self._data.pop(entity_id, None)

    def exists(self, entity_id: str) -> bool:
        """Check if entity exists."""
        return entity_id in self._data

    def find_by_criteria(self, criteria: dict[str, Any]) -> list[dict[str, Any]]:
        """Find entities by criteria."""
        results = []
        for entity_data in self._data.values():
            if all(entity_data.get(k) == v for k, v in criteria.items()):
                results.append(entity_data)
        return results

    def save_batch(self, entities: dict[str, dict[str, Any]]) -> None:
        """Save multiple entities."""
        for entity_id, data in entities.items():
            self._data[entity_id] = data.copy()

    def delete_batch(self, entity_ids: list[str]) -> None:
        """Delete multiple entities."""
        for entity_id in entity_ids:
            self._data.pop(entity_id, None)

    def begin_transaction(self) -> None:
        """Begin a transaction."""
        super().begin_transaction()

    def commit_transaction(self) -> None:
        """Commit the current transaction."""
        super().commit_transaction()

    def rollback_transaction(self) -> None:
        """Rollback the current transaction."""
        super().rollback_transaction()

    def count(self) -> int:
        """Count total entities."""
        return len(self._data)


class TestStorageReader:
    """Test StorageReader interface."""

    def test_interface_methods_exist(self):
        """Test that StorageReader has required methods."""
        assert hasattr(StorageReader, "find_by_id")
        assert hasattr(StorageReader, "find_all")
        assert hasattr(StorageReader, "exists")
        assert hasattr(StorageReader, "find_by_criteria")

    def test_interface_is_abstract(self):
        """Test that StorageReader cannot be instantiated directly."""
        with pytest.raises(TypeError):
            StorageReader()


class TestStorageWriter:
    """Test StorageWriter interface."""

    def test_interface_methods_exist(self):
        """Test that StorageWriter has required methods."""
        assert hasattr(StorageWriter, "save")
        assert hasattr(StorageWriter, "delete")

    def test_interface_is_abstract(self):
        """Test that StorageWriter cannot be instantiated directly."""
        with pytest.raises(TypeError):
            StorageWriter()


class TestBatchStorage:
    """Test BatchStorage interface."""

    def test_interface_methods_exist(self):
        """Test that BatchStorage has required methods."""
        assert hasattr(BatchStorage, "save_batch")
        assert hasattr(BatchStorage, "delete_batch")

    def test_interface_is_abstract(self):
        """Test that BatchStorage cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BatchStorage()


class TestTransactionalStorage:
    """Test TransactionalStorage interface."""

    def test_interface_methods_exist(self):
        """Test that TransactionalStorage has required methods."""
        assert hasattr(TransactionalStorage, "begin_transaction")
        assert hasattr(TransactionalStorage, "commit_transaction")
        assert hasattr(TransactionalStorage, "rollback_transaction")

    def test_interface_is_abstract(self):
        """Test that TransactionalStorage cannot be instantiated directly."""
        with pytest.raises(TypeError):
            TransactionalStorage()


class TestStorageStrategyAdapter:
    """Test StorageStrategyAdapter."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_strategy = MockStorageStrategy()
        self.adapter = StorageStrategyAdapter(self.mock_strategy)

    def test_adapter_implements_all_interfaces(self):
        """Test that adapter implements all segregated interfaces."""
        assert isinstance(self.adapter, StorageReader)
        assert isinstance(self.adapter, StorageWriter)
        assert isinstance(self.adapter, BatchStorage)
        assert isinstance(self.adapter, TransactionalStorage)

    def test_reader_operations(self):
        """Test reader operations through adapter."""
        # Setup test data
        test_data = {"id": "test-1", "name": "Test Entity"}
        self.mock_strategy.save("test-1", test_data)

        # Test find_by_id
        result = self.adapter.find_by_id("test-1")
        assert result == test_data

        # Test exists
        assert self.adapter.exists("test-1") is True
        assert self.adapter.exists("nonexistent") is False

        # Test find_all
        all_data = self.adapter.find_all()
        assert "test-1" in all_data
        assert all_data["test-1"] == test_data

        # Test find_by_criteria
        results = self.adapter.find_by_criteria({"name": "Test Entity"})
        assert len(results) == 1
        assert results[0] == test_data

    def test_writer_operations(self):
        """Test writer operations through adapter."""
        test_data = {"id": "test-2", "name": "Test Entity 2"}

        # Test save
        self.adapter.save("test-2", test_data)
        assert self.mock_strategy.find_by_id("test-2") == test_data

        # Test delete
        self.adapter.delete("test-2")
        assert self.mock_strategy.find_by_id("test-2") is None

    def test_batch_operations(self):
        """Test batch operations through adapter."""
        test_entities = {
            "batch-1": {"id": "batch-1", "name": "Batch Entity 1"},
            "batch-2": {"id": "batch-2", "name": "Batch Entity 2"},
        }

        # Test save_batch
        self.adapter.save_batch(test_entities)
        assert self.mock_strategy.find_by_id("batch-1") == test_entities["batch-1"]
        assert self.mock_strategy.find_by_id("batch-2") == test_entities["batch-2"]

        # Test delete_batch
        self.adapter.delete_batch(["batch-1", "batch-2"])
        assert self.mock_strategy.find_by_id("batch-1") is None
        assert self.mock_strategy.find_by_id("batch-2") is None

    def test_transaction_operations(self):
        """Test transaction operations through adapter."""
        # Test begin_transaction
        self.adapter.begin_transaction()
        assert self.mock_strategy._in_transaction is True

        # Test commit_transaction
        self.adapter.commit_transaction()
        assert self.mock_strategy._in_transaction is False

        # Test rollback_transaction
        self.adapter.begin_transaction()
        self.adapter.rollback_transaction()
        assert self.mock_strategy._in_transaction is False

    def test_adapter_delegates_to_strategy(self):
        """Test that adapter properly delegates to underlying strategy."""
        # Create a mock strategy to verify method calls
        mock_strategy = Mock(spec=StorageStrategy)
        adapter = StorageStrategyAdapter(mock_strategy)

        # Test delegation for each interface method
        adapter.find_by_id("test-id")
        mock_strategy.find_by_id.assert_called_once_with("test-id")

        adapter.save("test-id", {"data": "test"})
        mock_strategy.save.assert_called_once_with("test-id", {"data": "test"})

        adapter.delete("test-id")
        mock_strategy.delete.assert_called_once_with("test-id")

        adapter.exists("test-id")
        mock_strategy.exists.assert_called_once_with("test-id")

        adapter.find_all()
        mock_strategy.find_all.assert_called_once()

        adapter.find_by_criteria({"field": "value"})
        mock_strategy.find_by_criteria.assert_called_once_with({"field": "value"})

        adapter.save_batch({"id": {"data": "test"}})
        mock_strategy.save_batch.assert_called_once_with({"id": {"data": "test"}})

        adapter.delete_batch(["id1", "id2"])
        mock_strategy.delete_batch.assert_called_once_with(["id1", "id2"])

        adapter.begin_transaction()
        mock_strategy.begin_transaction.assert_called_once()

        adapter.commit_transaction()
        mock_strategy.commit_transaction.assert_called_once()

        adapter.rollback_transaction()
        mock_strategy.rollback_transaction.assert_called_once()
