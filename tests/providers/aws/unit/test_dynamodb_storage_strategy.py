"""Unit tests for DynamoDBStorageStrategy.

Covers save, find_by_id, find_all, delete, exists, find_by_criteria,
save_batch, delete_batch, is_healthy, count, and transaction methods.

All DynamoDB client/resource calls are replaced with MagicMock.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from orb.infrastructure.storage.exceptions import StorageError
from orb.providers.aws.storage.strategy import DynamoDBStorageStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_error(code: str, msg: str = "err") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": msg}}, "Op")


def _make_strategy(table_exists=False):
    """Build a DynamoDBStorageStrategy with fully mocked AWS deps."""
    aws_client = MagicMock()
    logger = MagicMock()

    # Patch _initialize_table to prevent real AWS calls during construction
    with patch.object(DynamoDBStorageStrategy, "_initialize_table", return_value=None):
        strat = DynamoDBStorageStrategy(
            logger=logger,
            aws_client=aws_client,
            region="us-east-1",
            table_name="test-table",
        )

    # Replace components with mocks post-construction
    strat.client_manager = MagicMock()
    strat.converter = MagicMock()
    strat.transaction_manager = MagicMock()

    # LockManager context managers must work
    strat.lock_manager = MagicMock()
    strat.lock_manager.write_lock.return_value.__enter__ = MagicMock(return_value=None)
    strat.lock_manager.write_lock.return_value.__exit__ = MagicMock(return_value=False)
    strat.lock_manager.read_lock.return_value.__enter__ = MagicMock(return_value=None)
    strat.lock_manager.read_lock.return_value.__exit__ = MagicMock(return_value=False)

    return strat


# ---------------------------------------------------------------------------
# is_healthy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsHealthy:
    def test_healthy_when_client_manager_healthy_and_table_exists(self):
        strat = _make_strategy()
        strat.client_manager.is_healthy.return_value = True
        strat.client_manager.table_exists.return_value = True
        healthy, details = strat.is_healthy()
        assert healthy is True
        assert details["table_exists"] is True

    def test_unhealthy_when_client_manager_not_healthy(self):
        strat = _make_strategy()
        strat.client_manager.is_healthy.return_value = False
        healthy, details = strat.is_healthy()
        assert healthy is False
        assert "reason" in details

    def test_unhealthy_when_table_does_not_exist(self):
        strat = _make_strategy()
        strat.client_manager.is_healthy.return_value = True
        strat.client_manager.table_exists.return_value = False
        healthy, details = strat.is_healthy()
        assert healthy is False

    def test_unhealthy_on_table_exists_exception(self):
        strat = _make_strategy()
        strat.client_manager.is_healthy.return_value = True
        strat.client_manager.table_exists.side_effect = RuntimeError("describe failed")
        healthy, details = strat.is_healthy()
        assert healthy is False
        assert "error" in details

    def test_unhealthy_on_outer_exception(self):
        strat = _make_strategy()
        strat.client_manager.is_healthy.side_effect = RuntimeError("boom")
        healthy, details = strat.is_healthy()
        assert healthy is False


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSave:
    def test_save_calls_put_item(self):
        strat = _make_strategy()
        strat.converter.to_dynamodb_item.return_value = {"id": "e-1", "val": "x"}
        strat.client_manager.put_item.return_value = True
        strat.save("e-1", {"val": "x"})
        strat.client_manager.put_item.assert_called_once_with(
            "test-table", {"id": "e-1", "val": "x"}
        )

    def test_save_raises_storage_error_when_put_fails(self):
        strat = _make_strategy()
        strat.converter.to_dynamodb_item.return_value = {"id": "e-1"}
        strat.client_manager.put_item.return_value = False
        with pytest.raises(StorageError):
            strat.save("e-1", {})

    def test_save_handles_client_error(self):
        strat = _make_strategy()
        strat.converter.to_dynamodb_item.return_value = {"id": "e-1"}
        strat.client_manager.put_item.side_effect = _client_error("ValidationException")
        strat.client_manager.handle_client_error.return_value = None
        with pytest.raises(StorageError):
            strat.save("e-1", {})

    def test_save_handles_generic_exception(self):
        strat = _make_strategy()
        strat.converter.to_dynamodb_item.return_value = {"id": "e-1"}
        strat.client_manager.put_item.side_effect = RuntimeError("network error")
        with pytest.raises(StorageError):
            strat.save("e-1", {})


# ---------------------------------------------------------------------------
# find_by_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindById:
    def test_returns_entity_when_found(self):
        strat = _make_strategy()
        strat.converter.get_key.return_value = {"id": "e-1"}
        strat.client_manager.get_item.return_value = {"id": "e-1", "val": "x"}
        strat.converter.from_dynamodb_item.return_value = {"id": "e-1", "val": "x"}
        result = strat.find_by_id("e-1")
        assert result == {"id": "e-1", "val": "x"}

    def test_returns_none_when_not_found(self):
        strat = _make_strategy()
        strat.converter.get_key.return_value = {"id": "missing"}
        strat.client_manager.get_item.return_value = None
        result = strat.find_by_id("missing")
        assert result is None

    def test_returns_none_on_client_error(self):
        strat = _make_strategy()
        strat.converter.get_key.return_value = {"id": "e-1"}
        strat.client_manager.get_item.side_effect = _client_error("ResourceNotFoundException")
        strat.client_manager.handle_client_error.return_value = None
        result = strat.find_by_id("e-1")
        assert result is None

    def test_returns_none_on_generic_exception(self):
        strat = _make_strategy()
        strat.converter.get_key.return_value = {"id": "e-1"}
        strat.client_manager.get_item.side_effect = RuntimeError("db err")
        result = strat.find_by_id("e-1")
        assert result is None


# ---------------------------------------------------------------------------
# find_all
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindAll:
    def test_returns_all_entities(self):
        strat = _make_strategy()
        raw = [{"id": "e-1"}, {"id": "e-2"}]
        strat.client_manager.scan_table.return_value = raw
        strat.converter.from_dynamodb_item.side_effect = lambda x: x
        strat.converter.extract_entity_id.side_effect = lambda x: x["id"]
        result = strat.find_all()
        assert len(result) == 2
        assert "e-1" in result

    def test_returns_empty_on_client_error(self):
        strat = _make_strategy()
        strat.client_manager.scan_table.side_effect = _client_error("ServiceUnavailable")
        strat.client_manager.handle_client_error.return_value = None
        result = strat.find_all()
        assert result == {}

    def test_returns_empty_on_generic_exception(self):
        strat = _make_strategy()
        strat.client_manager.scan_table.side_effect = RuntimeError("scan err")
        result = strat.find_all()
        assert result == {}

    def test_skips_items_without_entity_id(self):
        strat = _make_strategy()
        strat.client_manager.scan_table.return_value = [{"id": "e-1"}, {}]
        strat.converter.from_dynamodb_item.side_effect = lambda x: x
        strat.converter.extract_entity_id.side_effect = lambda x: x.get("id")
        result = strat.find_all()
        assert "e-1" in result
        assert None not in result


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDelete:
    def test_delete_calls_delete_item(self):
        strat = _make_strategy()
        strat.converter.get_key.return_value = {"id": "e-1"}
        strat.client_manager.delete_item.return_value = True
        strat.delete("e-1")
        strat.client_manager.delete_item.assert_called_once()

    def test_delete_logs_warning_when_not_found(self):
        strat = _make_strategy()
        strat.converter.get_key.return_value = {"id": "e-1"}
        strat.client_manager.delete_item.return_value = False
        strat.delete("e-1")  # should not raise
        strat._logger.warning.assert_called()

    def test_delete_raises_storage_error_on_client_error(self):
        strat = _make_strategy()
        strat.converter.get_key.return_value = {"id": "e-1"}
        strat.client_manager.delete_item.side_effect = _client_error("ValidationException")
        strat.client_manager.handle_client_error.return_value = None
        with pytest.raises(StorageError):
            strat.delete("e-1")

    def test_delete_raises_storage_error_on_generic_exception(self):
        strat = _make_strategy()
        strat.converter.get_key.return_value = {"id": "e-1"}
        strat.client_manager.delete_item.side_effect = RuntimeError("del err")
        with pytest.raises(StorageError):
            strat.delete("e-1")


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExists:
    def test_returns_true_when_item_found(self):
        strat = _make_strategy()
        strat.converter.get_key.return_value = {"id": "e-1"}
        strat.client_manager.get_item.return_value = {"id": "e-1"}
        assert strat.exists("e-1") is True

    def test_returns_false_when_item_not_found(self):
        strat = _make_strategy()
        strat.converter.get_key.return_value = {"id": "missing"}
        strat.client_manager.get_item.return_value = None
        assert strat.exists("missing") is False

    def test_returns_false_on_exception(self):
        strat = _make_strategy()
        strat.converter.get_key.return_value = {"id": "e-1"}
        strat.client_manager.get_item.side_effect = RuntimeError("boom")
        assert strat.exists("e-1") is False


# ---------------------------------------------------------------------------
# find_by_criteria
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindByCriteria:
    def test_returns_matching_entities(self):
        strat = _make_strategy()
        strat.converter.build_filter_expression.return_value = ("filter_expr", {})
        strat.client_manager.scan_table.return_value = [{"id": "e-1"}]
        strat.converter.from_dynamodb_items.return_value = [{"id": "e-1"}]
        result = strat.find_by_criteria({"status": "active"})
        assert len(result) == 1

    def test_returns_empty_on_client_error(self):
        strat = _make_strategy()
        strat.converter.build_filter_expression.return_value = ("f", {})
        strat.client_manager.scan_table.side_effect = _client_error("ResourceNotFoundException")
        strat.client_manager.handle_client_error.return_value = None
        result = strat.find_by_criteria({"x": "y"})
        assert result == []

    def test_returns_empty_on_generic_exception(self):
        strat = _make_strategy()
        strat.converter.build_filter_expression.return_value = ("f", {})
        strat.client_manager.scan_table.side_effect = RuntimeError("scan err")
        result = strat.find_by_criteria({"x": "y"})
        assert result == []


# ---------------------------------------------------------------------------
# save_batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSaveBatch:
    def test_save_batch_calls_batch_write(self):
        strat = _make_strategy()
        strat.converter.prepare_batch_items.return_value = [{"id": "e-1"}, {"id": "e-2"}]
        strat.client_manager.batch_write_items.return_value = True
        strat.save_batch({"e-1": {}, "e-2": {}})
        strat.client_manager.batch_write_items.assert_called_once()

    def test_save_batch_raises_storage_error_when_batch_fails(self):
        strat = _make_strategy()
        strat.converter.prepare_batch_items.return_value = []
        strat.client_manager.batch_write_items.return_value = False
        with pytest.raises(StorageError):
            strat.save_batch({"e-1": {}})

    def test_save_batch_raises_on_client_error(self):
        strat = _make_strategy()
        strat.converter.prepare_batch_items.return_value = []
        strat.client_manager.batch_write_items.side_effect = _client_error("ValidationException")
        strat.client_manager.handle_client_error.return_value = None
        with pytest.raises(StorageError):
            strat.save_batch({"e-1": {}})

    def test_save_batch_raises_on_generic_exception(self):
        strat = _make_strategy()
        strat.converter.prepare_batch_items.return_value = []
        strat.client_manager.batch_write_items.side_effect = RuntimeError("net err")
        with pytest.raises(StorageError):
            strat.save_batch({"e-1": {}})


# ---------------------------------------------------------------------------
# delete_batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteBatch:
    def test_delete_batch_calls_transaction_operations(self):
        strat = _make_strategy()
        ctx_mgr = MagicMock()
        ctx_mgr.__enter__ = MagicMock(return_value=None)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        strat.transaction_manager.atomic_operation.return_value = ctx_mgr
        strat.converter.get_key.return_value = {"id": "e-1"}
        strat.delete_batch(["e-1"])
        strat.transaction_manager.add_delete_item.assert_called_once()

    def test_delete_batch_raises_on_client_error(self):
        strat = _make_strategy()
        ctx_mgr = MagicMock()
        ctx_mgr.__enter__ = MagicMock(return_value=None)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        strat.transaction_manager.atomic_operation.return_value = ctx_mgr
        strat.converter.get_key.side_effect = _client_error("ValidationException")
        strat.client_manager.handle_client_error.return_value = None
        with pytest.raises(StorageError):
            strat.delete_batch(["e-1"])

    def test_delete_batch_raises_on_generic_exception(self):
        strat = _make_strategy()
        ctx_mgr = MagicMock()
        ctx_mgr.__enter__ = MagicMock(return_value=None)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        strat.transaction_manager.atomic_operation.return_value = ctx_mgr
        strat.converter.get_key.side_effect = RuntimeError("del err")
        with pytest.raises(StorageError):
            strat.delete_batch(["e-1"])


# ---------------------------------------------------------------------------
# Transaction methods
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTransactionMethods:
    def test_begin_transaction_delegates(self):
        strat = _make_strategy()
        strat.begin_transaction()
        strat.transaction_manager.begin_transaction.assert_called_once()

    def test_commit_transaction_delegates(self):
        strat = _make_strategy()
        strat.commit_transaction()
        strat.transaction_manager.commit_transaction.assert_called_once()

    def test_rollback_transaction_delegates(self):
        strat = _make_strategy()
        strat.rollback_transaction()
        strat.transaction_manager.rollback_transaction.assert_called_once()


# ---------------------------------------------------------------------------
# count / cleanup / get_table_name / get_client_manager / get_transaction_manager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMiscMethods:
    def test_count_returns_length_of_scan(self):
        strat = _make_strategy()
        strat.client_manager.scan_table.return_value = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        assert strat.count() == 3

    def test_count_returns_zero_on_exception(self):
        strat = _make_strategy()
        strat.client_manager.scan_table.side_effect = RuntimeError("err")
        assert strat.count() == 0

    def test_cleanup_does_not_raise(self):
        strat = _make_strategy()
        strat.cleanup()  # should complete without exception

    def test_get_table_name(self):
        strat = _make_strategy()
        assert strat.get_table_name() == "test-table"

    def test_get_client_manager_returns_component(self):
        strat = _make_strategy()
        assert strat.get_client_manager() is strat.client_manager

    def test_get_transaction_manager_returns_component(self):
        strat = _make_strategy()
        assert strat.get_transaction_manager() is strat.transaction_manager
