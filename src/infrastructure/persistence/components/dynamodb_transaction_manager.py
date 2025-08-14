"""DynamoDB transaction management components."""

from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional

from botocore.exceptions import ClientError

from .transaction_manager import TransactionManager, TransactionState


class DynamoDBTransactionManager(TransactionManager):
    """
    DynamoDB transaction manager for handling DynamoDB transactions.

    Supports both single-table and cross-table transactions using DynamoDB's
    TransactWrite and TransactRead operations.
    """

    def __init__(self, client_manager):
        """
        Initialize DynamoDB transaction manager.

        Args:
            client_manager: DynamoDB client manager
        """
        super().__init__()
        self.client_manager = client_manager
        self.transaction_items: List[Dict[str, Any]] = []
        self.max_transaction_items = 25  # DynamoDB limit

    def begin_transaction(self) -> None:
        """Begin a new DynamoDB transaction."""
        if self.state == TransactionState.ACTIVE:
            raise RuntimeError("Transaction already active")

        self.state = TransactionState.ACTIVE
        self.transaction_items.clear()
        self.logger.debug("DynamoDB transaction begun")

    def add_put_item(
        self,
        table_name: str,
        item: Dict[str, Any],
        condition_expression: Optional[str] = None,
    ) -> None:
        """
        Add put item operation to transaction.

        Args:
            table_name: Name of the table
            item: Item to put
            condition_expression: Optional condition expression
        """
        if self.state != TransactionState.ACTIVE:
            raise RuntimeError("No active transaction")

        if len(self.transaction_items) >= self.max_transaction_items:
            raise RuntimeError(f"Transaction cannot exceed {self.max_transaction_items} items")

        put_request = {"Put": {"TableName": table_name, "Item": item}}

        if condition_expression:
            put_request["Put"]["ConditionExpression"] = condition_expression

        self.transaction_items.append(put_request)
        self.logger.debug(f"Added put item to transaction for table {table_name}")

    def add_update_item(
        self,
        table_name: str,
        key: Dict[str, Any],
        update_expression: str,
        expression_attribute_values: Dict[str, Any],
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
        if self.state != TransactionState.ACTIVE:
            raise RuntimeError("No active transaction")

        if len(self.transaction_items) >= self.max_transaction_items:
            raise RuntimeError(f"Transaction cannot exceed {self.max_transaction_items} items")

        update_request = {
            "Update": {
                "TableName": table_name,
                "Key": key,
                "UpdateExpression": update_expression,
                "ExpressionAttributeValues": expression_attribute_values,
            }
        }

        if condition_expression:
            update_request["Update"]["ConditionExpression"] = condition_expression

        self.transaction_items.append(update_request)
        self.logger.debug(f"Added update item to transaction for table {table_name}")

    def add_delete_item(
        self,
        table_name: str,
        key: Dict[str, Any],
        condition_expression: Optional[str] = None,
    ) -> None:
        """
        Add delete item operation to transaction.

        Args:
            table_name: Name of the table
            key: Primary key of item to delete
            condition_expression: Optional condition expression
        """
        if self.state != TransactionState.ACTIVE:
            raise RuntimeError("No active transaction")

        if len(self.transaction_items) >= self.max_transaction_items:
            raise RuntimeError(f"Transaction cannot exceed {self.max_transaction_items} items")

        delete_request = {"Delete": {"TableName": table_name, "Key": key}}

        if condition_expression:
            delete_request["Delete"]["ConditionExpression"] = condition_expression

        self.transaction_items.append(delete_request)
        self.logger.debug(f"Added delete item to transaction for table {table_name}")

    def commit_transaction(self) -> None:
        """Commit the current DynamoDB transaction."""
        if self.state != TransactionState.ACTIVE:
            raise RuntimeError("No active transaction to commit")

        if not self.transaction_items:
            self.state = TransactionState.COMMITTED
            self.logger.debug("Empty DynamoDB transaction committed")
            return

        try:
            # Execute transaction using TransactWrite
            dynamodb_client = self.client_manager.get_client()

            response = dynamodb_client.transact_write_items(TransactItems=self.transaction_items)

            # Validate response and log transaction details
            if response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200:
                self.state = TransactionState.COMMITTED
                self.logger.debug(
                    f"DynamoDB transaction committed successfully with {len(self.transaction_items)} operations",
                    extra={"request_id": response.get("ResponseMetadata", {}).get("RequestId")},
                )
            else:
                self.state = TransactionState.FAILED
                self.logger.error(
                    f"DynamoDB transaction failed with status: {response.get('ResponseMetadata', {}).get('HTTPStatusCode')}",
                    extra={"response": response},
                )

        except ClientError as e:
            self.state = TransactionState.FAILED
            error_code = e.response["Error"]["Code"]

            if error_code == "TransactionCanceledException":
                # Handle transaction cancellation reasons
                cancellation_reasons = e.response.get("CancellationReasons", [])
                self.logger.error(f"DynamoDB transaction cancelled: {cancellation_reasons}")
            else:
                self.logger.error(
                    f"DynamoDB transaction failed: {error_code} - {e.response['Error']['Message']}"
                )

            raise
        except Exception as e:
            self.state = TransactionState.FAILED
            self.logger.error(f"DynamoDB transaction commit failed: {e}")
            raise
        finally:
            self.transaction_items.clear()

    def rollback_transaction(self) -> None:
        """Rollback the current DynamoDB transaction."""
        if self.state != TransactionState.ACTIVE:
            self.logger.warning("No active DynamoDB transaction to rollback")
            return

        # DynamoDB transactions are atomic - if they fail, they're automatically rolled back
        # We just need to clean up our state
        self.state = TransactionState.ROLLED_BACK
        self.transaction_items.clear()
        self.logger.debug("DynamoDB transaction rolled back (cleared pending operations)")

    def execute_read_transaction(self, read_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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

            self.logger.debug(f"Executed read transaction with {len(read_items)} operations")
            return results

        except ClientError as e:
            self.logger.error(f"DynamoDB read transaction failed: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Read transaction execution failed: {e}")
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
            self.logger.error(f"Batch operation failed: {e}")
            raise

    @contextmanager
    def atomic_operation(self):
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
            self.logger.error(f"Atomic operation failed: {e}")
            raise

    def get_transaction_size(self) -> int:
        """Get current transaction size."""
        return len(self.transaction_items)

    def can_add_items(self, count: int) -> bool:
        """
        Check if we can add more items to transaction.

        Args:
            count: Number of items to add

        Returns:
            True if items can be added, False otherwise
        """
        return len(self.transaction_items) + count <= self.max_transaction_items
