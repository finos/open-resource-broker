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
from orb.infrastructure.storage.exceptions import TransactionError
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

    def test_non_200_response_raises_and_marks_failed(self, txn, client_manager):
        client_manager.get_client.return_value.transact_write_items.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 500}
        }
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        with pytest.raises(TransactionError) as exc_info:
            txn.commit_transaction()
        assert "500" in exc_info.value.error_code
        assert txn.state == TransactionState.FAILED

    def test_client_error_transaction_cancelled_raises_transaction_error_and_marks_failed(
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
        with pytest.raises(TransactionError) as exc_info:
            txn.commit_transaction()
        assert isinstance(exc_info.value.__cause__, ClientError)
        assert txn.state == TransactionState.FAILED

    def test_client_error_other_code_raises_transaction_error_and_marks_failed(
        self, txn, client_manager
    ):
        error = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "bad request"}},
            "TransactWriteItems",
        )
        client_manager.get_client.return_value.transact_write_items.side_effect = error
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        with pytest.raises(TransactionError) as exc_info:
            txn.commit_transaction()
        assert isinstance(exc_info.value.__cause__, ClientError)
        assert txn.state == TransactionState.FAILED

    def test_generic_exception_raises_transaction_error_and_marks_failed(self, txn, client_manager):
        client_manager.get_client.return_value.transact_write_items.side_effect = RuntimeError(
            "boom"
        )
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        with pytest.raises(TransactionError) as exc_info:
            txn.commit_transaction()
        # Only the exception class is reported: the text of non-service errors
        # (e.g. botocore validation) can echo parameter values
        assert "RuntimeError" in exc_info.value.error_code
        assert "boom" not in exc_info.value.error_code
        assert txn.state == TransactionState.FAILED

    def test_commit_clears_items_even_on_failure(self, txn, client_manager):
        client_manager.get_client.return_value.transact_write_items.side_effect = RuntimeError(
            "boom"
        )
        txn.begin_transaction()
        txn.add_put_item("table", {"id": "1"})
        with pytest.raises(TransactionError):
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
# get_transaction_size
# ---------------------------------------------------------------------------


class TestTransactionSizeHelpers:
    def test_get_transaction_size_reflects_items(self, txn):
        txn.begin_transaction()
        assert txn.get_transaction_size() == 0
        txn.add_put_item("table", {"id": "1"})
        assert txn.get_transaction_size() == 1


# ---------------------------------------------------------------------------
# merging actions on the same item
# ---------------------------------------------------------------------------


class TestSameItemMerging:
    def test_later_payload_wins_and_first_condition_is_kept(self, txn):
        txn.begin_transaction()
        txn.add_put_item("t", {"id": "1", "v": 2}, condition_expression="v = :old", key={"id": "1"})
        txn.add_put_item("t", {"id": "1", "v": 3}, key={"id": "1"})

        assert txn.get_transaction_size() == 1
        put = txn.transaction_items[0]["Put"]
        assert put["Item"]["v"] == {"N": "3"}
        assert put["ConditionExpression"] == "v = :old"

    def test_later_condition_is_not_applied_over_the_first(self, txn):
        txn.begin_transaction()
        txn.add_put_item("t", {"id": "1"}, key={"id": "1"})
        txn.add_put_item("t", {"id": "1"}, condition_expression="x = :y", key={"id": "1"})

        assert "ConditionExpression" not in txn.transaction_items[0]["Put"]

    def test_update_over_conditional_put_keeps_condition_and_its_values(self, txn):
        txn.begin_transaction()
        txn.add_put_item(
            "t", {"id": "1"}, condition_expression="attribute_not_exists(id)", key={"id": "1"}
        )
        txn.add_update_item("t", {"id": "1"}, "SET v = :v", {":v": 2}, condition_expression=None)

        update = txn.transaction_items[0]["Update"]
        assert update["ConditionExpression"] == "attribute_not_exists(id)"
        assert update["ExpressionAttributeValues"] == {":v": {"N": "2"}}

    def test_condition_values_of_first_update_survive_a_later_update(self, txn):
        txn.begin_transaction()
        txn.add_update_item(
            "t",
            {"id": "1"},
            "SET version = :new",
            {":new": 2, ":expected": 1},
            condition_expression="version = :expected",
        )
        txn.add_update_item("t", {"id": "1"}, "SET version = :next", {":next": 3})

        update = txn.transaction_items[0]["Update"]
        assert update["ConditionExpression"] == "version = :expected"
        assert update["UpdateExpression"] == "SET version = :next"
        assert update["ExpressionAttributeValues"] == {
            ":expected": {"N": "1"},
            ":next": {"N": "3"},
        }

    def test_put_over_conditional_update_drops_values_not_used_by_the_condition(self, txn):
        txn.begin_transaction()
        txn.add_update_item(
            "t",
            {"id": "1"},
            "SET version = :new",
            {":new": 2, ":expected": 1},
            condition_expression="version = :expected",
        )
        txn.add_put_item("t", {"id": "1", "version": 2}, key={"id": "1"})

        put = txn.transaction_items[0]["Put"]
        assert put["ConditionExpression"] == "version = :expected"
        assert put["ExpressionAttributeValues"] == {":expected": {"N": "1"}}

    def test_conflicting_attribute_values_for_one_item_are_rejected(self, txn):
        txn.begin_transaction()
        txn.add_update_item(
            "t", {"id": "1"}, "SET a = :v", {":v": 1}, condition_expression="a = :v"
        )
        with pytest.raises(TransactionError):
            txn.add_update_item("t", {"id": "1"}, "SET b = :v", {":v": 2})

    def test_put_then_delete_becomes_delete(self, txn):
        txn.begin_transaction()
        txn.add_put_item("t", {"id": "1"}, key={"id": "1"})
        txn.add_delete_item("t", {"id": "1"})

        assert [next(iter(i)) for i in txn.transaction_items] == ["Delete"]

    def test_delete_then_put_becomes_put(self, txn):
        txn.begin_transaction()
        txn.add_delete_item("t", {"id": "1"})
        txn.add_put_item("t", {"id": "1"}, key={"id": "1"})

        assert [next(iter(i)) for i in txn.transaction_items] == ["Put"]

    def test_same_key_in_different_tables_is_not_merged(self, txn):
        txn.begin_transaction()
        txn.add_put_item("a", {"id": "1"}, key={"id": "1"})
        txn.add_put_item("b", {"id": "1"}, key={"id": "1"})

        assert txn.get_transaction_size() == 2

    def test_merged_action_does_not_consume_capacity(self, txn):
        txn.max_transaction_items = 1
        txn.begin_transaction()
        txn.add_put_item("t", {"id": "1"}, key={"id": "1"})
        txn.add_put_item("t", {"id": "1", "v": 1}, key={"id": "1"})

        assert txn.get_transaction_size() == 1


# ---------------------------------------------------------------------------
# retry behaviour
# ---------------------------------------------------------------------------


def _cancelled(*codes: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
            "CancellationReasons": [{"Code": c} for c in codes],
        },
        "TransactWriteItems",
    )


def _top_level(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "m"}}, "TransactWriteItems")


_OK = {"ResponseMetadata": {"HTTPStatusCode": 200}}


class TestCommitRetry:
    @pytest.fixture
    def sleeps(self) -> list[float]:
        return []

    @pytest.fixture
    def retry_txn(self, client_manager, sleeps) -> DynamoDBTransactionManager:
        manager = DynamoDBTransactionManager(client_manager, sleep=sleeps.append)
        manager.begin_transaction()
        manager.add_put_item("t", {"id": "1"})
        return manager

    @pytest.mark.parametrize(
        "error",
        [
            _cancelled("TransactionConflict", "None"),
            _cancelled("ProvisionedThroughputExceeded"),
            _cancelled("ThrottlingError", "None", "TransactionConflict"),
            _top_level("TransactionInProgressException"),
            _top_level("ProvisionedThroughputExceededException"),
            _top_level("ThrottlingException"),
            _top_level("RequestLimitExceeded"),
        ],
    )
    def test_transient_failure_is_retried_until_success(
        self, retry_txn, client_manager, sleeps, error
    ):
        client = client_manager.get_client.return_value
        client.transact_write_items.side_effect = [error, _OK]

        retry_txn.commit_transaction()

        assert client.transact_write_items.call_count == 2
        assert len(sleeps) == 1
        assert retry_txn.state == TransactionState.COMMITTED

    @pytest.mark.parametrize(
        "error",
        [
            _cancelled("ConditionalCheckFailed", "None"),
            _cancelled("TransactionConflict", "ConditionalCheckFailed"),
            _cancelled("ValidationError"),
            _cancelled("ItemCollectionSizeLimitExceeded"),
            _cancelled(),
            _top_level("ValidationException"),
            _top_level("AccessDeniedException"),
        ],
    )
    def test_other_failures_are_not_retried(self, retry_txn, client_manager, sleeps, error):
        client = client_manager.get_client.return_value
        client.transact_write_items.side_effect = error

        with pytest.raises(TransactionError) as exc_info:
            retry_txn.commit_transaction()

        assert client.transact_write_items.call_count == 1
        assert sleeps == []
        assert exc_info.value.__cause__ is error

    def test_gives_up_after_max_attempts_and_reports_cause(self, retry_txn, client_manager, sleeps):
        error = _cancelled("TransactionConflict")
        client = client_manager.get_client.return_value
        client.transact_write_items.side_effect = error

        with pytest.raises(TransactionError) as exc_info:
            retry_txn.commit_transaction()

        assert client.transact_write_items.call_count == retry_txn.max_commit_attempts
        assert len(sleeps) == retry_txn.max_commit_attempts - 1
        assert exc_info.value.__cause__ is error
        assert retry_txn.state == TransactionState.FAILED

    def test_failure_message_names_reasons_but_not_item_data(self, client_manager):
        client_manager.get_client.return_value.transact_write_items.side_effect = _cancelled(
            "ConditionalCheckFailed"
        )
        manager = DynamoDBTransactionManager(client_manager)
        manager.begin_transaction()
        manager.add_put_item("t", {"id": "1", "secret": "s3cr3t"})

        with pytest.raises(TransactionError) as exc_info:
            manager.commit_transaction()

        assert "ConditionalCheckFailed" in exc_info.value.error_code
        assert "s3cr3t" not in exc_info.value.error_code


# ---------------------------------------------------------------------------
# chunked commits
# ---------------------------------------------------------------------------


def _calls(client_manager) -> list[list[dict]]:
    client = client_manager.get_client.return_value
    return [c.kwargs["TransactItems"] for c in client.transact_write_items.call_args_list]


def _tables(call: list[dict]) -> list[str]:
    return [next(iter(a.values()))["TableName"] for a in call]


class TestChunkedCommit:
    def test_up_to_the_limit_is_a_single_call(self, txn, client_manager):
        txn.begin_transaction()
        for i in range(100):
            txn.add_put_item("m", {"id": str(i)}, key={"id": str(i)})
        txn.commit_transaction()

        assert [len(c) for c in _calls(client_manager)] == [100]

    def test_larger_commits_are_split_at_the_action_limit(self, txn, client_manager):
        txn.begin_transaction()
        for i in range(250):
            txn.add_put_item("m", {"id": str(i)}, key={"id": str(i)})
        txn.commit_transaction()

        assert [len(c) for c in _calls(client_manager)] == [100, 100, 50]
        assert txn.state == TransactionState.COMMITTED

    def test_split_respects_the_size_budget(self, txn, client_manager):
        txn.max_transaction_bytes = 5_000
        txn.begin_transaction()
        for i in range(6):
            txn.add_put_item("m", {"id": str(i), "blob": "x" * 1_500}, key={"id": str(i)})
        txn.commit_transaction()

        calls = _calls(client_manager)
        assert len(calls) > 1
        assert sum(len(c) for c in calls) == 6
        assert all(len(c) < 6 for c in calls)

    def test_one_oversized_action_still_gets_its_own_call(self, txn, client_manager):
        txn.max_transaction_bytes = 100
        txn.begin_transaction()
        txn.add_put_item("m", {"id": "1", "blob": "x" * 1_000}, key={"id": "1"})
        txn.commit_transaction()

        assert [len(c) for c in _calls(client_manager)] == [1]

    def test_anchor_puts_first_other_tables_next_anchor_deletes_last(self, txn, client_manager):
        txn.anchor_table = "requests"
        txn.begin_transaction()
        txn.add_delete_item("requests", {"id": "old"})
        txn.add_put_item("machines", {"id": "m1"}, key={"id": "m1"})
        txn.add_delete_item("machines", {"id": "m0"})
        txn.add_put_item("requests", {"id": "r1"}, key={"id": "r1"})
        txn.commit_transaction()

        kinds = [
            (next(iter(a)), next(iter(a.values()))["TableName"]) for a in _calls(client_manager)[0]
        ]
        assert kinds == [
            ("Put", "requests"),
            ("Put", "machines"),
            ("Delete", "machines"),
            ("Delete", "requests"),
        ]

    def test_dedupe_happens_before_chunking(self, txn, client_manager):
        txn.begin_transaction()
        txn.add_put_item("m", {"id": "dup", "v": 1}, key={"id": "dup"})
        for i in range(150):
            txn.add_put_item("m", {"id": str(i)}, key={"id": str(i)})
        txn.add_put_item("m", {"id": "dup", "v": 2}, key={"id": "dup"})
        txn.commit_transaction()

        calls = _calls(client_manager)
        assert sum(len(c) for c in calls) == 151
        keys = [next(iter(a.values()))["Item"]["id"]["S"] for c in calls for a in c]
        assert keys.count("dup") == 1

    def test_later_call_failure_reports_progress_and_unapplied_rows(self, client_manager, caplog):
        txn = DynamoDBTransactionManager(client_manager, sleep=lambda _s: None)
        txn.anchor_table = "requests"
        client = client_manager.get_client.return_value
        client.transact_write_items.side_effect = [_OK, _top_level("ValidationException")]
        txn.begin_transaction()
        txn.add_put_item("requests", {"id": "r-1"}, key={"id": "r-1"})
        for i in range(150):
            txn.add_put_item("machines", {"id": f"m-{i}", "secret": "s3cr3t"}, key={"id": f"m-{i}"})
        txn.add_delete_item("requests", {"id": "r-old"})

        with caplog.at_level("ERROR"):
            with pytest.raises(TransactionError) as exc_info:
                txn.commit_transaction()

        message = exc_info.value.error_code
        assert "call 2 of 2" in message and "1 earlier call(s) were applied" in message
        assert "'machines': 51" in message and "'requests': 1" in message
        assert "r-old" in message
        assert "s3cr3t" not in message
        # str() shows the same detail (operators see it in logs and CLI output)
        assert str(exc_info.value) == message
        assert "call 2 of 2" in caplog.text
        assert txn.state == TransactionState.FAILED

    def test_each_call_is_retried_independently(self, client_manager):
        sleeps: list[float] = []
        txn = DynamoDBTransactionManager(client_manager, sleep=sleeps.append)
        client = client_manager.get_client.return_value
        client.transact_write_items.side_effect = [_OK, _cancelled("TransactionConflict"), _OK]
        txn.begin_transaction()
        for i in range(101):
            txn.add_put_item("m", {"id": str(i)}, key={"id": str(i)})

        txn.commit_transaction()

        assert client.transact_write_items.call_count == 3
        assert len(sleeps) == 1
