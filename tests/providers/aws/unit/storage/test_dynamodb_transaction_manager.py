"""Unit tests for DynamoDBTransactionManager.

Exercises real transaction-building logic (serialization via boto3's
TypeSerializer, item-count limits, state transitions) with a mocked
``client_manager`` boundary rather than mocking the manager itself.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from orb.infrastructure.storage.components.transaction_manager import TransactionState
from orb.providers.aws.storage.components.dynamodb_transaction_manager import (
    DynamoDBTransactionManager,
)


@pytest.fixture
def client_manager():
    manager = MagicMock()
    client = MagicMock()
    client.transact_write_items.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "req-1"}
    }
    manager.get_client.return_value = client
    return manager


@pytest.fixture
def txn(client_manager) -> DynamoDBTransactionManager:
    return DynamoDBTransactionManager(client_manager)


# ---------------------------------------------------------------------------
# begin_transaction
# ---------------------------------------------------------------------------


class TestBeginTransaction:
    def test_sets_state_active(self, txn):
        txn.begin_transaction()
        assert txn.state == TransactionState.ACTIVE

    def test_clears_items_left_over_from_a_rolled_back_transaction(self, txn):
        txn.begin_transaction()
        txn.add_put_item("t", {"id": "1"})
        txn.rollback_transaction()
        txn.begin_transaction()
        assert txn.get_transaction_size() == 0

    def test_rejects_double_begin(self, txn):
        txn.begin_transaction()
        with pytest.raises(RuntimeError, match="already active"):
            txn.begin_transaction()


# ---------------------------------------------------------------------------
# add_put_item / add_update_item / add_delete_item
# ---------------------------------------------------------------------------


class TestAddPutItem:
    def test_requires_active_transaction(self, txn):
        with pytest.raises(RuntimeError, match="No active transaction"):
            txn.add_put_item("table", {"id": "1"})

    def test_serializes_item_with_type_tags(self, txn):
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "abc", "count": 3})
        assert txn.get_transaction_size() == 1
        put = txn.transaction_items[0]["Put"]
        assert put["TableName"] == "table"
        assert put["Item"]["id"] == {"S": "abc"}
        assert put["Item"]["count"] == {"N": "3"}

    def test_includes_condition_expression_when_given(self, txn):
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "abc"}, condition_expression="attribute_not_exists(id)")
        put = txn.transaction_items[0]["Put"]
        assert put["ConditionExpression"] == "attribute_not_exists(id)"

    def test_omits_condition_expression_when_not_given(self, txn):
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "abc"})
        put = txn.transaction_items[0]["Put"]
        assert "ConditionExpression" not in put

    def test_rejects_when_at_max_items(self, txn):
        txn.begin_transaction()
        txn.max_transaction_items = 1
        txn.add_put_item("table", {"id": "1"})
        with pytest.raises(RuntimeError, match="cannot exceed"):
            txn.add_put_item("table", {"id": "2"})


class TestAddUpdateItem:
    def test_requires_active_transaction(self, txn):
        with pytest.raises(RuntimeError, match="No active transaction"):
            txn.add_update_item("table", {"id": "1"}, "SET #n = :v", {":v": "x"})

    def test_serializes_key_and_values(self, txn):
        txn.begin_transaction()
        txn.add_update_item(
            "table",
            {"id": "abc"},
            "SET #n = :v",
            {":v": "new-value"},
        )
        update = txn.transaction_items[0]["Update"]
        assert update["TableName"] == "table"
        assert update["Key"]["id"] == {"S": "abc"}
        assert update["UpdateExpression"] == "SET #n = :v"
        assert update["ExpressionAttributeValues"][":v"] == {"S": "new-value"}

    def test_includes_condition_expression_when_given(self, txn):
        txn.begin_transaction()
        txn.add_update_item(
            "table",
            {"id": "abc"},
            "SET #n = :v",
            {":v": "x"},
            condition_expression="attribute_exists(id)",
        )
        update = txn.transaction_items[0]["Update"]
        assert update["ConditionExpression"] == "attribute_exists(id)"

    def test_rejects_when_at_max_items(self, txn):
        txn.begin_transaction()
        txn.max_transaction_items = 1
        txn.add_update_item("table", {"id": "1"}, "SET #n = :v", {":v": "x"})
        with pytest.raises(RuntimeError, match="cannot exceed"):
            txn.add_update_item("table", {"id": "2"}, "SET #n = :v", {":v": "y"})


class TestAddDeleteItem:
    def test_requires_active_transaction(self, txn):
        with pytest.raises(RuntimeError, match="No active transaction"):
            txn.add_delete_item("table", {"id": "1"})

    def test_serializes_key(self, txn):
        txn.begin_transaction()
        txn.add_delete_item("table", {"id": "abc"})
        delete = txn.transaction_items[0]["Delete"]
        assert delete["TableName"] == "table"
        assert delete["Key"]["id"] == {"S": "abc"}

    def test_includes_condition_expression_when_given(self, txn):
        txn.begin_transaction()
        txn.add_delete_item("table", {"id": "abc"}, condition_expression="attribute_exists(id)")
        delete = txn.transaction_items[0]["Delete"]
        assert delete["ConditionExpression"] == "attribute_exists(id)"

    def test_rejects_when_at_max_items(self, txn):
        txn.begin_transaction()
        txn.max_transaction_items = 1
        txn.add_delete_item("table", {"id": "1"})
        with pytest.raises(RuntimeError, match="cannot exceed"):
            txn.add_delete_item("table", {"id": "2"})


# ---------------------------------------------------------------------------
# commit_transaction
# ---------------------------------------------------------------------------


class TestCommitTransaction:
    def test_requires_active_transaction(self, txn):
        with pytest.raises(RuntimeError, match="No active transaction to commit"):
            txn.commit_transaction()

    def test_empty_transaction_commits_without_client_call(self, txn, client_manager):
        txn.begin_transaction()
        txn.commit_transaction()
        assert txn.state == TransactionState.COMMITTED
        client_manager.get_client.assert_not_called()

    def test_successful_commit_sets_committed_state(self, txn, client_manager):
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        txn.commit_transaction()
        assert txn.state == TransactionState.COMMITTED
        client_manager.get_client.return_value.transact_write_items.assert_called_once()

    def test_commit_clears_transaction_items_on_success(self, txn):
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        txn.commit_transaction()
        assert txn.get_transaction_size() == 0

    def test_non_200_response_marks_failed(self, txn, client_manager):
        client_manager.get_client.return_value.transact_write_items.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 500}
        }
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        txn.commit_transaction()
        assert txn.state == TransactionState.FAILED

    def test_client_error_transaction_cancelled_reraises_and_marks_failed(
        self, txn, client_manager
    ):
        error = ClientError(
            {
                "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
                "CancellationReasons": [{"Code": "ConditionalCheckFailed"}],
            },
            "TransactWriteItems",
        )
        client_manager.get_client.return_value.transact_write_items.side_effect = error
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        with pytest.raises(ClientError):
            txn.commit_transaction()
        assert txn.state == TransactionState.FAILED

    def test_client_error_other_code_reraises_and_marks_failed(self, txn, client_manager):
        error = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "bad request"}},
            "TransactWriteItems",
        )
        client_manager.get_client.return_value.transact_write_items.side_effect = error
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        with pytest.raises(ClientError):
            txn.commit_transaction()
        assert txn.state == TransactionState.FAILED

    def test_generic_exception_reraises_and_marks_failed(self, txn, client_manager):
        client_manager.get_client.return_value.transact_write_items.side_effect = RuntimeError(
            "boom"
        )
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        with pytest.raises(RuntimeError, match="boom"):
            txn.commit_transaction()
        assert txn.state == TransactionState.FAILED

    def test_commit_clears_items_even_on_failure(self, txn, client_manager):
        client_manager.get_client.return_value.transact_write_items.side_effect = RuntimeError(
            "boom"
        )
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        with pytest.raises(RuntimeError):
            txn.commit_transaction()
        assert txn.get_transaction_size() == 0


# ---------------------------------------------------------------------------
# rollback_transaction
# ---------------------------------------------------------------------------


class TestRollbackTransaction:
    def test_rollback_without_active_transaction_is_noop(self, txn):
        txn.rollback_transaction()  # must not raise
        assert txn.state == TransactionState.INACTIVE

    def test_rollback_sets_rolled_back_state(self, txn):
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        txn.rollback_transaction()
        assert txn.state == TransactionState.ROLLED_BACK

    def test_rollback_clears_pending_items(self, txn):
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        txn.rollback_transaction()
        assert txn.get_transaction_size() == 0


# ---------------------------------------------------------------------------
# execute_read_transaction
# ---------------------------------------------------------------------------


class TestExecuteReadTransaction:
    def test_returns_items_from_response(self, txn, client_manager):
        client_manager.get_client.return_value.transact_get_items.return_value = {
            "Responses": [{"Item": {"id": {"S": "a"}}}, {"Item": {"id": {"S": "b"}}}]
        }
        results = txn.execute_read_transaction([{"Get": {"TableName": "t", "Key": {}}}])
        assert len(results) == 2

    def test_skips_responses_without_item(self, txn, client_manager):
        client_manager.get_client.return_value.transact_get_items.return_value = {
            "Responses": [{"Item": {"id": {"S": "a"}}}, {}]
        }
        results = txn.execute_read_transaction([{"Get": {"TableName": "t", "Key": {}}}])
        assert len(results) == 1

    def test_rejects_too_many_read_items(self, txn):
        read_items = [{"Get": {}}] * (txn.max_transaction_items + 1)
        with pytest.raises(RuntimeError, match="cannot exceed"):
            txn.execute_read_transaction(read_items)

    def test_client_error_reraises(self, txn, client_manager):
        error = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "bad"}}, "TransactGetItems"
        )
        client_manager.get_client.return_value.transact_get_items.side_effect = error
        with pytest.raises(ClientError):
            txn.execute_read_transaction([{"Get": {}}])

    def test_generic_exception_reraises(self, txn, client_manager):
        client_manager.get_client.return_value.transact_get_items.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            txn.execute_read_transaction([{"Get": {}}])


# ---------------------------------------------------------------------------
# execute_batch_operation
# ---------------------------------------------------------------------------


class TestExecuteBatchOperation:
    def test_returns_operation_result(self, txn):
        result = txn.execute_batch_operation(lambda: "done")
        assert result == "done"

    def test_reraises_operation_exception(self, txn):
        def boom():
            raise ValueError("bad op")

        with pytest.raises(ValueError, match="bad op"):
            txn.execute_batch_operation(boom)


# ---------------------------------------------------------------------------
# atomic_operation context manager
# ---------------------------------------------------------------------------


class TestAtomicOperation:
    def test_commits_on_success(self, txn, client_manager):
        with txn.atomic_operation():
            txn.add_put_item("table", {"id": "1"})
        assert txn.state == TransactionState.COMMITTED

    def test_rolls_back_on_exception(self, txn):
        def fail_after_adding_item():
            txn.add_put_item("table", {"id": "1"})
            raise ValueError("failure inside")

        with pytest.raises(ValueError, match="failure inside"):
            with txn.atomic_operation():
                fail_after_adding_item()

        assert txn.state == TransactionState.ROLLED_BACK
        assert txn.get_transaction_size() == 0


# ---------------------------------------------------------------------------
# get_transaction_size / can_add_items
# ---------------------------------------------------------------------------


class TestTransactionSizeHelpers:
    def test_get_transaction_size_reflects_items(self, txn):
        txn.begin_transaction()
        assert txn.get_transaction_size() == 0
        txn.add_put_item("table", {"id": "1"})
        assert txn.get_transaction_size() == 1

    def test_can_add_items_true_when_under_limit(self, txn):
        assert txn.can_add_items(5) is True

    def test_can_add_items_false_when_over_limit(self, txn):
        txn.max_transaction_items = 2
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        txn.add_put_item("table", {"id": "2"})
        assert txn.can_add_items(1) is False

    def test_can_add_items_true_at_exact_limit(self, txn):
        txn.max_transaction_items = 2
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        assert txn.can_add_items(1) is True
