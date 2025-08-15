"""Transaction management components for storage operations."""

from abc import ABC, abstractmethod
from contextlib import contextmanager
from enum import Enum
from typing import Any, Callable, List, Optional

from infrastructure.logging.logger import get_logger


class TransactionState(str, Enum):
    """Transaction state enumeration."""

    INACTIVE = "inactive"
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class TransactionManager(ABC):
    """Base interface for transaction managers."""

    def __init__(self):
        """Initialize the instance."""
        self.logger = get_logger(__name__)
        self.state = TransactionState.INACTIVE

    @abstractmethod
    def begin_transaction(self) -> None:
        """Begin a new transaction."""

    @abstractmethod
    def commit_transaction(self) -> None:
        """Commit the current transaction."""

    @abstractmethod
    def rollback_transaction(self) -> None:
        """Rollback the current transaction."""

    @contextmanager
    def transaction(self):
        """Context manager for transaction handling."""
        self.begin_transaction()
        try:
            yield
            self.commit_transaction()
        except Exception as e:
            self.logger.error(f"Transaction failed: {e}")
            self.rollback_transaction()
            raise

    def execute_in_transaction(self, operation: Callable[[], Any]) -> Any:
        """Execute operation within a transaction."""
        with self.transaction():
            return operation()


class MemoryTransactionManager(TransactionManager):
    """In-memory transaction manager for testing and simple operations."""

    def __init__(self):
        """Initialize in-memory transaction manager."""
        super().__init__()
        self.operations: List[Callable[[], None]] = []
        self.rollback_operations: List[Callable[[], None]] = []

    def begin_transaction(self) -> None:
        """Begin a new transaction."""
        if self.state == TransactionState.ACTIVE:
            raise RuntimeError("Transaction already active")

        self.state = TransactionState.ACTIVE
        self.operations.clear()
        self.rollback_operations.clear()
        self.logger.debug("Memory transaction begun")

    def commit_transaction(self) -> None:
        """Commit the current transaction."""
        if self.state != TransactionState.ACTIVE:
            raise RuntimeError("No active transaction to commit")

        try:
            # Execute all operations
            for operation in self.operations:
                operation()

            self.state = TransactionState.COMMITTED
            self.logger.debug(
                f"Memory transaction committed with {len(self.operations)} operations"
            )
        except Exception as e:
            self.state = TransactionState.FAILED
            self.logger.error(f"Memory transaction commit failed: {e}")
            raise
        finally:
            self.operations.clear()
            self.rollback_operations.clear()

    def rollback_transaction(self) -> None:
        """Rollback the current transaction."""
        if self.state != TransactionState.ACTIVE:
            self.logger.warning("No active transaction to rollback")
            return

        try:
            # Execute rollback operations in reverse order
            for rollback_op in reversed(self.rollback_operations):
                try:
                    rollback_op()
                except Exception as e:
                    self.logger.error(f"Rollback operation failed: {e}")

            self.state = TransactionState.ROLLED_BACK
            self.logger.debug(
                f"Memory transaction rolled back with {len(self.rollback_operations)} rollback operations"
            )
        except Exception as e:
            self.state = TransactionState.FAILED
            self.logger.error(f"Memory transaction rollback failed: {e}")
        finally:
            self.operations.clear()
            self.rollback_operations.clear()

    def add_operation(
        self,
        operation: Callable[[], None],
        rollback_operation: Optional[Callable[[], None]] = None,
    ):
        """Add operation to transaction."""
        if self.state != TransactionState.ACTIVE:
            raise RuntimeError("No active transaction")

        self.operations.append(operation)
        if rollback_operation:
            self.rollback_operations.append(rollback_operation)


class NoOpTransactionManager(TransactionManager):
    """No-operation transaction manager for storage that doesn't support transactions."""

    def begin_transaction(self) -> None:
        """Begin transaction (no-op)."""
        self.state = TransactionState.ACTIVE
        self.logger.debug("No-op transaction begun")

    def commit_transaction(self) -> None:
        """Commit transaction (no-op)."""
        self.state = TransactionState.COMMITTED
        self.logger.debug("No-op transaction committed")

    def rollback_transaction(self) -> None:
        """Rollback transaction (no-op)."""
        self.state = TransactionState.ROLLED_BACK
        self.logger.debug("No-op transaction rolled back")
