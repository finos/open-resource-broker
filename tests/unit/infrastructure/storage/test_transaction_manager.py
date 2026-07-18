"""Unit tests for transaction manager components (components/transaction_manager.py)."""

import pytest

from orb.infrastructure.storage.components.transaction_manager import (
    MemoryTransactionManager,
    NoOpTransactionManager,
    TransactionState,
)

# ---------------------------------------------------------------------------
# MemoryTransactionManager — basic state transitions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMemoryTransactionManagerBegin:
    def test_begin_sets_active_state(self) -> None:
        mgr = MemoryTransactionManager()
        mgr.begin_transaction()
        assert mgr.state == TransactionState.ACTIVE

    def test_begin_clears_previous_operations(self) -> None:
        mgr = MemoryTransactionManager()
        mgr.begin_transaction()
        mgr.add_operation(lambda: None)
        mgr.rollback_transaction()
        mgr.begin_transaction()
        assert len(mgr.operations) == 0

    def test_begin_twice_raises(self) -> None:
        mgr = MemoryTransactionManager()
        mgr.begin_transaction()
        with pytest.raises(RuntimeError, match="already active"):
            mgr.begin_transaction()


@pytest.mark.unit
class TestMemoryTransactionManagerCommit:
    def test_commit_executes_operations(self) -> None:
        calls: list[int] = []
        mgr = MemoryTransactionManager()
        mgr.begin_transaction()
        mgr.add_operation(lambda: calls.append(1))
        mgr.add_operation(lambda: calls.append(2))
        mgr.commit_transaction()
        assert calls == [1, 2]

    def test_commit_sets_committed_state(self) -> None:
        mgr = MemoryTransactionManager()
        mgr.begin_transaction()
        mgr.commit_transaction()
        assert mgr.state == TransactionState.COMMITTED

    def test_commit_without_begin_raises(self) -> None:
        mgr = MemoryTransactionManager()
        with pytest.raises(RuntimeError, match="No active transaction"):
            mgr.commit_transaction()

    def test_commit_clears_operations(self) -> None:
        mgr = MemoryTransactionManager()
        mgr.begin_transaction()
        mgr.add_operation(lambda: None)
        mgr.commit_transaction()
        assert mgr.operations == []

    def test_commit_sets_failed_state_on_operation_exception(self) -> None:
        def _boom():
            raise RuntimeError("op failed")

        mgr = MemoryTransactionManager()
        mgr.begin_transaction()
        mgr.add_operation(_boom)
        with pytest.raises(RuntimeError, match="op failed"):
            mgr.commit_transaction()
        assert mgr.state == TransactionState.FAILED


@pytest.mark.unit
class TestMemoryTransactionManagerRollback:
    def test_rollback_sets_rolled_back_state(self) -> None:
        mgr = MemoryTransactionManager()
        mgr.begin_transaction()
        mgr.rollback_transaction()
        assert mgr.state == TransactionState.ROLLED_BACK

    def test_rollback_executes_rollback_operations_in_reverse(self) -> None:
        order: list[int] = []
        mgr = MemoryTransactionManager()
        mgr.begin_transaction()
        mgr.add_operation(lambda: None, rollback_operation=lambda: order.append(1))
        mgr.add_operation(lambda: None, rollback_operation=lambda: order.append(2))
        mgr.rollback_transaction()
        assert order == [2, 1]

    def test_rollback_when_not_active_logs_warning(self) -> None:
        """Rolling back without an active transaction should not raise."""
        mgr = MemoryTransactionManager()
        mgr.rollback_transaction()  # should not raise

    def test_rollback_clears_operations(self) -> None:
        mgr = MemoryTransactionManager()
        mgr.begin_transaction()
        mgr.add_operation(lambda: None)
        mgr.rollback_transaction()
        assert mgr.operations == []
        assert mgr.rollback_operations == []

    def test_rollback_with_failing_rollback_op_continues(self) -> None:
        """A failing rollback operation should not prevent the state change."""
        mgr = MemoryTransactionManager()
        mgr.begin_transaction()
        mgr.add_operation(
            lambda: None, rollback_operation=lambda: (_ for _ in ()).throw(RuntimeError("rb fail"))
        )  # type: ignore[misc]
        mgr.rollback_transaction()  # must not propagate the error
        assert mgr.state == TransactionState.ROLLED_BACK


@pytest.mark.unit
class TestMemoryTransactionManagerAddOperation:
    def test_add_operation_outside_transaction_raises(self) -> None:
        mgr = MemoryTransactionManager()
        with pytest.raises(RuntimeError, match="No active transaction"):
            mgr.add_operation(lambda: None)

    def test_add_operation_with_rollback(self) -> None:
        rb_calls: list[bool] = []
        mgr = MemoryTransactionManager()
        mgr.begin_transaction()
        mgr.add_operation(lambda: None, rollback_operation=lambda: rb_calls.append(True))
        assert len(mgr.rollback_operations) == 1

    def test_add_operation_without_rollback_not_added_to_rollback_list(self) -> None:
        mgr = MemoryTransactionManager()
        mgr.begin_transaction()
        mgr.add_operation(lambda: None)
        assert mgr.rollback_operations == []


@pytest.mark.unit
class TestMemoryTransactionManagerContextManager:
    def test_context_manager_commits_on_success(self) -> None:
        calls: list[str] = []
        mgr = MemoryTransactionManager()
        with mgr.transaction():
            mgr.add_operation(lambda: calls.append("done"))
        assert calls == ["done"]
        assert mgr.state == TransactionState.COMMITTED

    def test_context_manager_rolls_back_on_exception(self) -> None:
        mgr = MemoryTransactionManager()
        with pytest.raises(ValueError):
            with mgr.transaction():
                raise ValueError("fail")
        assert mgr.state == TransactionState.ROLLED_BACK

    def test_execute_in_transaction(self) -> None:
        results: list[int] = []
        mgr = MemoryTransactionManager()

        def _op():
            results.append(42)
            return 42

        # execute_in_transaction begins its own transaction context
        value = mgr.execute_in_transaction(_op)
        assert value == 42


# ---------------------------------------------------------------------------
# NoOpTransactionManager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoOpTransactionManager:
    def test_begin_sets_active(self) -> None:
        mgr = NoOpTransactionManager()
        mgr.begin_transaction()
        assert mgr.state == TransactionState.ACTIVE

    def test_commit_sets_committed(self) -> None:
        mgr = NoOpTransactionManager()
        mgr.begin_transaction()
        mgr.commit_transaction()
        assert mgr.state == TransactionState.COMMITTED

    def test_rollback_sets_rolled_back(self) -> None:
        mgr = NoOpTransactionManager()
        mgr.begin_transaction()
        mgr.rollback_transaction()
        assert mgr.state == TransactionState.ROLLED_BACK

    def test_context_manager_commit(self) -> None:
        mgr = NoOpTransactionManager()
        with mgr.transaction():
            pass
        assert mgr.state == TransactionState.COMMITTED

    def test_context_manager_rollback_on_exception(self) -> None:
        mgr = NoOpTransactionManager()
        with pytest.raises(RuntimeError):
            with mgr.transaction():
                raise RuntimeError("oops")
        assert mgr.state == TransactionState.ROLLED_BACK

    def test_execute_in_transaction_returns_value(self) -> None:
        mgr = NoOpTransactionManager()
        result = mgr.execute_in_transaction(lambda: 99)
        assert result == 99
