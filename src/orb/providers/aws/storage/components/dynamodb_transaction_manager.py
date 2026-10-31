"""DynamoDB transaction management components."""

import json
import random
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Optional

from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from orb.infrastructure.storage.components.transaction_manager import (
    TransactionManager,
    TransactionState,
)
from orb.infrastructure.storage.exceptions import TransactionError

# DynamoDB TransactWriteItems accepts at most this many actions per call.
DYNAMODB_MAX_TRANSACTION_ITEMS = 100

# The aggregate request size limit is 4 MB; the estimate below is approximate
# (it measures the JSON form of each action), so stay clear of the limit.
DYNAMODB_MAX_TRANSACTION_BYTES = 3_500_000

# Cancellation reason codes that indicate a transient condition: the same
# request can succeed when sent again.
_RETRYABLE_CANCELLATION_CODES = frozenset(
    {"TransactionConflict", "ProvisionedThroughputExceeded", "ThrottlingError"}
)
# Top-level error codes that are safe to retry.
_RETRYABLE_ERROR_CODES = frozenset(
    {
        "TransactionInProgressException",
        "ProvisionedThroughputExceededException",
        "ThrottlingException",
        "RequestLimitExceeded",
    }
)


class DynamoDBTransactionManager(TransactionManager):
    """
    DynamoDB transaction manager for handling DynamoDB transactions.

    Operations staged between ``begin_transaction`` and ``commit_transaction``
    (possibly against several tables) are sent as a single
    ``TransactWriteItems`` call, so they are applied all together or not at
    all. A single manager instance may be shared by several storage
    strategies to make a multi-table unit of work atomic.

    A commit of at most ``max_transaction_items`` actions (and within the size
    budget) is one atomic call. A larger commit is split into several calls,
    each atomic on its own, so it is NOT atomic as a whole: if a later call
    fails, the earlier ones stay applied and ``TransactionError`` reports how far
    the commit got. Actions on ``anchor_table`` are ordered so that such a
    partial commit never leaves other items without their anchoring row on
    writes, nor a half-deleted set without its anchoring row on deletes: puts and
    updates of the anchor table go first, deletes of it go last. This also
    applies to ``atomic_operation`` and to batch deletes made outside a unit of
    work.

    Staging and committing are intended to be driven from one thread; an
    internal lock only protects the staged list from interleaved appends.
    """

    max_commit_attempts = 3
    retry_base_delay = 0.05

    def __init__(
        self,
        client_manager,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        """
        Initialize DynamoDB transaction manager.

        Args:
            client_manager: DynamoDB client manager
            sleep: Callable used to back off between retries (defaults to time.sleep)
        """
        super().__init__()
        self.client_manager = client_manager
        self.transaction_items: list[dict[str, Any]] = []
        self.max_transaction_items = DYNAMODB_MAX_TRANSACTION_ITEMS
        self.max_transaction_bytes = DYNAMODB_MAX_TRANSACTION_BYTES
        # Table whose rows anchor the other tables' rows (the request table).
        self.anchor_table: Optional[str] = None
        self._sleep = sleep or time.sleep
        self._lock = threading.RLock()
        # (table, serialized key) -> position in transaction_items
        self._staged_keys: dict[tuple[str, str], int] = {}
        # id(action) -> plain key, used only to report unapplied rows on failure
        self._action_keys: dict[int, dict[str, Any]] = {}
        # transact_write_items uses the low-level Client API, which requires
        # AttributeValue-tagged dicts (e.g. {"id": {"S": "abc"}}). The
        # higher-level Resource API (used elsewhere) accepts plain dicts;
        # this serializer converts at the boundary.
        self._serializer = TypeSerializer()

    @property
    def is_active(self) -> bool:
        """True while operations may be staged."""
        return self.state == TransactionState.ACTIVE

    def begin_transaction(self) -> None:
        """Begin a new DynamoDB transaction."""
        if self.state == TransactionState.ACTIVE:
            raise RuntimeError("Transaction already active")

        self.state = TransactionState.ACTIVE
        self._reset_staged()
        self.logger.debug("DynamoDB transaction begun")

    def _reset_staged(self) -> None:
        with self._lock:
            self.transaction_items.clear()
            self._staged_keys.clear()
            self._action_keys.clear()

    def _serialize_key(self, key: dict[str, Any]) -> dict[str, Any]:
        return {k: self._serializer.serialize(v) for k, v in key.items()}

    def _stage(
        self,
        table_name: str,
        request_type: str,
        request: dict[str, Any],
        key: Optional[dict[str, Any]],
    ) -> None:
        """Stage one action, merging with an earlier action on the same item.

        DynamoDB rejects a transaction with two actions on one item. When the
        same item is staged twice the later action's payload wins (equivalent
        to applying them in order) while the first action's condition is kept,
        since a condition guards the state the item had before the transaction.
        """
        if self.state != TransactionState.ACTIVE:
            raise RuntimeError("No active transaction")

        with self._lock:
            ident: Optional[tuple[str, str]] = None
            if key is not None:
                ident = (
                    table_name,
                    json.dumps(self._serialize_key(key), sort_keys=True, default=str),
                )
                position = self._staged_keys.get(ident)
                if position is not None:
                    first = next(iter(self.transaction_items[position].values()))
                    self._inherit_condition(request, first)
                    self._action_keys.pop(id(self.transaction_items[position]), None)
                    action = {request_type: request}
                    self.transaction_items[position] = action
                    if key is not None:
                        self._action_keys[id(action)] = key
                    return

            action = {request_type: request}
            self.transaction_items.append(action)
            if key is not None:
                self._action_keys[id(action)] = key
            if ident is not None:
                self._staged_keys[ident] = len(self.transaction_items) - 1

    @staticmethod
    def _inherit_condition(request: dict[str, Any], first: dict[str, Any]) -> None:
        """Make ``request`` carry ``first``'s condition, and nothing else of its own.

        The condition's expression attribute names and values are copied from
        ``first``; those that only served ``request``'s own dropped condition are
        removed, since DynamoDB rejects expression attributes that are not used.
        """
        request.pop("ConditionExpression", None)
        condition = first.get("ConditionExpression")
        if condition:
            request["ConditionExpression"] = condition

        used_text = " ".join(
            str(request.get(field, "")) for field in ("UpdateExpression", "ConditionExpression")
        )
        for field in ("ExpressionAttributeNames", "ExpressionAttributeValues"):
            merged: dict[str, Any] = {}
            for source in (first if condition else {}, request):
                for token, value in source.get(field, {}).items():
                    if re.search(re.escape(token) + r"(?![A-Za-z0-9_])", used_text):
                        if token in merged and merged[token] != value:
                            raise TransactionError(
                                f"Conflicting expression attribute {token} for one item "
                                "staged twice in a transaction"
                            )
                        merged[token] = value
            if merged:
                request[field] = merged
            else:
                request.pop(field, None)

    def add_put_item(
        self,
        table_name: str,
        item: dict[str, Any],
        condition_expression: Optional[str] = None,
        key: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Add put item operation to transaction.

        Args:
            table_name: Name of the table
            item: Item to put
            condition_expression: Optional condition expression
            key: Primary key of the item; enables merging with other staged
                actions on the same item
        """
        serialized_item = {k: self._serializer.serialize(v) for k, v in item.items()}
        put: dict[str, Any] = {"TableName": table_name, "Item": serialized_item}
        if condition_expression:
            put["ConditionExpression"] = condition_expression

        self._stage(table_name, "Put", put, key)
        self.logger.debug("Added put item to transaction for table %s", table_name)

    def add_update_item(
        self,
        table_name: str,
        key: dict[str, Any],
        update_expression: str,
        expression_attribute_values: dict[str, Any],
        condition_expression: Optional[str] = None,
    ) -> None:
        """
        Add update item operation to transaction.

        Args:
            table_name: Name of the table
            key: Primary key of item to update
            update_expression: Update expression
            expression_attribute_values: Expression attribute values
            condition_expression: Optional condition expression
        """
        serialized_values = {
            k: self._serializer.serialize(v) for k, v in expression_attribute_values.items()
        }
        update: dict[str, Any] = {
            "TableName": table_name,
            "Key": self._serialize_key(key),
            "UpdateExpression": update_expression,
            "ExpressionAttributeValues": serialized_values,
        }
        if condition_expression:
            update["ConditionExpression"] = condition_expression

        self._stage(table_name, "Update", update, key)
        self.logger.debug("Added update item to transaction for table %s", table_name)

    def add_delete_item(
        self,
        table_name: str,
        key: dict[str, Any],
        condition_expression: Optional[str] = None,
    ) -> None:
        """
        Add delete item operation to transaction.

        Args:
            table_name: Name of the table
            key: Primary key of item to delete
            condition_expression: Optional condition expression
        """
        delete: dict[str, Any] = {"TableName": table_name, "Key": self._serialize_key(key)}
        if condition_expression:
            delete["ConditionExpression"] = condition_expression

        self._stage(table_name, "Delete", delete, key)
        self.logger.debug("Added delete item to transaction for table %s", table_name)

    @staticmethod
    def _is_retryable(error: ClientError) -> bool:
        code = error.response.get("Error", {}).get("Code", "")
        if code == "TransactionCanceledException":
            reasons = error.response.get("CancellationReasons") or []
            if not reasons:
                return False
            codes = {r.get("Code") for r in reasons} - {"None", None}
            return codes <= _RETRYABLE_CANCELLATION_CODES
        return code in _RETRYABLE_ERROR_CODES

    @staticmethod
    def _describe(error: ClientError) -> str:
        code = error.response.get("Error", {}).get("Code", "Unknown")
        reasons = error.response.get("CancellationReasons")
        if reasons:
            return f"{code}: {[r.get('Code') for r in reasons]}"
        return f"{code}: {error.response.get('Error', {}).get('Message', '')}"

    def _ordered_actions(self) -> list[dict[str, Any]]:
        """Staged actions in commit order (stable within each group)."""

        def group(action: dict[str, Any]) -> int:
            kind, body = next(iter(action.items()))
            if body["TableName"] != self.anchor_table:
                return 1
            return 2 if kind == "Delete" else 0

        with self._lock:
            return sorted(self.transaction_items, key=group)

    @staticmethod
    def _action_size(action: dict[str, Any]) -> int:
        return len(json.dumps(action, default=str))

    def _chunks(self, actions: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """Split into calls of at most the action and size limits."""
        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_bytes = 0
        for action in actions:
            size = self._action_size(action)
            if current and (
                len(current) >= self.max_transaction_items
                or current_bytes + size > self.max_transaction_bytes
            ):
                chunks.append(current)
                current, current_bytes = [], 0
            current.append(action)
            current_bytes += size
        if current:
            chunks.append(current)
        return chunks

    def _send(self, client: Any, chunk: list[dict[str, Any]]) -> dict[str, Any]:
        """Send one TransactWriteItems call, retrying transient cancellations."""
        attempt = 0
        while True:
            attempt += 1
            try:
                return client.transact_write_items(TransactItems=chunk)
            except ClientError as e:
                if attempt < self.max_commit_attempts and self._is_retryable(e):
                    delay = self.retry_base_delay * (2 ** (attempt - 1))
                    self.logger.warning(
                        "DynamoDB transaction attempt %s/%s not applied (%s); retrying",
                        attempt,
                        self.max_commit_attempts,
                        self._describe(e),
                    )
                    self._sleep(delay + random.uniform(0, delay))  # nosec B311
                    continue
                raise

    def _unapplied_summary(self, chunks: list[list[dict[str, Any]]], from_index: int) -> str:
        """Describe the actions not applied: per-table counts and anchor-table keys."""
        per_table: dict[str, int] = {}
        anchor_keys: list[str] = []
        for chunk in chunks[from_index:]:
            for action in chunk:
                body = next(iter(action.values()))
                table = body["TableName"]
                per_table[table] = per_table.get(table, 0) + 1
                key = self._action_keys.get(id(action))
                if table == self.anchor_table and key is not None:
                    anchor_keys.append(json.dumps(key, sort_keys=True, default=str))
        return f"unapplied actions per table {per_table}, anchor table keys {anchor_keys}"

    def commit_transaction(self) -> None:
        """Commit the staged actions.

        Up to the action and size limits this is one atomic TransactWriteItems
        call. Larger commits are sent as several calls in a fixed order and are
        not atomic as a whole (see the class docstring). Transient cancellations
        (conflicts, throttling) are retried per call; any other failure is raised
        as TransactionError with the original error as its cause.
        """
        if self.state != TransactionState.ACTIVE:
            raise RuntimeError("No active transaction to commit")

        actions = self._ordered_actions()
        if not actions:
            self.state = TransactionState.COMMITTED
            self.logger.debug("Empty DynamoDB transaction committed")
            return

        chunks = self._chunks(actions)
        applied = 0
        try:
            dynamodb_client = self.client_manager.get_client()
            for chunk in chunks:
                response = self._send(dynamodb_client, chunk)
                status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if status != 200:
                    raise TransactionError(f"DynamoDB transaction returned HTTP status {status}")
                applied += 1

            self.state = TransactionState.COMMITTED
            self.logger.debug(
                "DynamoDB transaction committed with %s operations in %s call(s)",
                len(actions),
                len(chunks),
            )

        except Exception as e:
            self.state = TransactionState.FAILED
            if isinstance(e, ClientError):
                reason = self._describe(e)
            elif isinstance(e, TransactionError):
                reason = e.error_code
            else:
                # The class name only: botocore validation text can echo parameter values
                reason = type(e).__name__
            message = f"DynamoDB transaction failed: {reason}"
            if len(chunks) > 1:
                message += (
                    f" (call {applied + 1} of {len(chunks)}; {applied} earlier call(s) were "
                    f"applied and are not rolled back; "
                    f"{self._unapplied_summary(chunks, applied)})"
                )
            self.logger.error(message)
            raise TransactionError(message) from e
        finally:
            self._reset_staged()

    def rollback_transaction(self) -> None:
        """Rollback the current DynamoDB transaction."""
        if self.state != TransactionState.ACTIVE:
            self.logger.warning("No active DynamoDB transaction to rollback")
            return

        # Nothing is sent before commit, so discarding the staged actions is a
        # complete rollback.
        self.state = TransactionState.ROLLED_BACK
        self._reset_staged()
        self.logger.debug("DynamoDB transaction rolled back (cleared pending operations)")

    def execute_read_transaction(self, read_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Execute a read transaction.

        Args:
            read_items: List of read operations

        Returns:
            List of read results
        """
        try:
            if len(read_items) > self.max_transaction_items:
                raise RuntimeError(
                    f"Read transaction cannot exceed {self.max_transaction_items} items"
                )

            dynamodb_client = self.client_manager.get_client()

            response = dynamodb_client.transact_get_items(TransactItems=read_items)

            # Extract items from response
            results = []
            for item_response in response.get("Responses", []):
                item = item_response.get("Item")
                if item:
                    results.append(item)

            self.logger.debug("Executed read transaction with %s operations", len(read_items))
            return results

        except ClientError as e:
            self.logger.error("DynamoDB read transaction failed: %s", e)
            raise
        except Exception as e:
            self.logger.error("Read transaction execution failed: %s", e)
            raise

    def execute_batch_operation(self, operation: Callable[[], Any]) -> Any:
        """
        Execute operation as a batch (non-transactional).

        Args:
            operation: Operation to execute

        Returns:
            Operation result
        """
        try:
            return operation()
        except Exception as e:
            self.logger.error("Batch operation failed: %s", e)
            raise

    @contextmanager
    def atomic_operation(self):  # type: ignore[override]
        """
        Context manager for atomic DynamoDB operations.

        Automatically begins transaction, commits on success, or rolls back on failure.
        """
        self.begin_transaction()
        try:
            yield self
            self.commit_transaction()
        except Exception as e:
            self.rollback_transaction()
            self.logger.error("Atomic operation failed: %s", e)
            raise

    def get_transaction_size(self) -> int:
        """Get current transaction size."""
        return len(self.transaction_items)
