"""Unit tests for BaseUnitOfWork and StrategyUnitOfWork."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.storage.base.unit_of_work import (
    BaseUnitOfWork,
    StrategyUnitOfWork,
)
from orb.infrastructure.storage.exceptions import TransactionError

# ---------------------------------------------------------------------------
# Concrete subclass of BaseUnitOfWork for testing the abstract class.
# Must implement the two Protocol abstract properties: 'machines', 'requests'.
# ---------------------------------------------------------------------------


class _ConcreteUoW(BaseUnitOfWork):
    def __init__(self) -> None:
        super().__init__()
        self.began = 0
        self.committed = 0
        self.rolled_back = 0

    def _begin_transaction(self) -> None:
        self.began += 1

    def _commit_transaction(self) -> None:
        self.committed += 1

    def _rollback_transaction(self) -> None:
        self.rolled_back += 1

    @property
    def machines(self) -> Any:
        return None

    @property
    def requests(self) -> Any:
        return None

    @property
    def templates(self) -> Any:
        return None


class _ConcreteStrategyUoW(StrategyUnitOfWork):
    """Thin wrapper that satisfies the Protocol's machines/requests/templates properties."""

    @property
    def machines(self) -> Any:
        return None

    @property
    def requests(self) -> Any:
        return None

    @property
    def templates(self) -> Any:
        return None


# ---------------------------------------------------------------------------
# BaseUnitOfWork — begin / commit / rollback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseUnitOfWorkBeginCommitRollback:
    def test_begin_sets_in_transaction(self) -> None:
        uow = _ConcreteUoW()
        uow.begin()
        assert uow.in_transaction is True
        assert uow.began == 1

    def test_begin_twice_raises_transaction_error(self) -> None:
        uow = _ConcreteUoW()
        uow.begin()
        with pytest.raises(TransactionError):
            uow.begin()

    def test_commit_clears_in_transaction(self) -> None:
        uow = _ConcreteUoW()
        uow.begin()
        uow.commit()
        assert uow.in_transaction is False
        assert uow.committed == 1

    def test_commit_without_begin_raises(self) -> None:
        uow = _ConcreteUoW()
        with pytest.raises(TransactionError):
            uow.commit()

    def test_rollback_clears_in_transaction(self) -> None:
        uow = _ConcreteUoW()
        uow.begin()
        uow.rollback()
        assert uow.in_transaction is False
        assert uow.rolled_back == 1

    def test_rollback_without_begin_raises(self) -> None:
        uow = _ConcreteUoW()
        with pytest.raises(TransactionError):
            uow.rollback()

    def test_initial_state_not_in_transaction(self) -> None:
        uow = _ConcreteUoW()
        assert uow.in_transaction is False


# ---------------------------------------------------------------------------
# BaseUnitOfWork — context manager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseUnitOfWorkContextManager:
    def test_successful_block_calls_commit(self) -> None:
        uow = _ConcreteUoW()
        with uow:
            pass
        assert uow.committed == 1
        assert uow.rolled_back == 0

    def test_exception_in_block_calls_rollback_and_reraises(self) -> None:
        uow = _ConcreteUoW()

        def _raise_inside_transaction():
            with uow:
                raise RuntimeError("oops")

        with pytest.raises(RuntimeError):
            _raise_inside_transaction()
        assert uow.rolled_back == 1
        assert uow.committed == 0

    def test_enter_returns_uow_itself(self) -> None:
        uow = _ConcreteUoW()
        result = uow.__enter__()
        uow.commit()
        assert result is uow


# ---------------------------------------------------------------------------
# StrategyUnitOfWork — helpers
# ---------------------------------------------------------------------------


def _make_repo() -> MagicMock:
    """Make a mock repository backed by a storage strategy."""
    repo = MagicMock()
    repo.storage_strategy = MagicMock()
    return repo


# ---------------------------------------------------------------------------
# StrategyUnitOfWork._begin_transaction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStrategyUoWBegin:
    def test_storage_strategy_begin_called(self) -> None:
        repo = _make_repo()
        uow = _ConcreteStrategyUoW([repo])
        uow.begin()
        repo.storage_strategy.begin_transaction.assert_called_once()

    def test_repo_without_storage_strategy_attr_skipped(self) -> None:
        repo = MagicMock(spec=[])  # no storage_strategy attribute
        uow = _ConcreteStrategyUoW([repo])
        uow.begin()  # must not raise


# ---------------------------------------------------------------------------
# StrategyUnitOfWork._commit_transaction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStrategyUoWCommit:
    def test_storage_strategy_commit_called(self) -> None:
        repo = _make_repo()
        uow = _ConcreteStrategyUoW([repo])
        uow.begin()
        uow.commit()
        repo.storage_strategy.commit_transaction.assert_called_once()

    def test_concurrency_error_reaches_the_caller_unwrapped(self) -> None:
        from orb.domain.base.exceptions import ConcurrencyError

        repo = _make_repo()
        repo.storage_strategy.commit_transaction.side_effect = ConcurrencyError("stale")
        uow = _ConcreteStrategyUoW([repo])
        uow.begin()
        with pytest.raises(ConcurrencyError):
            uow.commit()

    def test_commit_exception_wrapped_as_transaction_error(self) -> None:
        repo = _make_repo()
        repo.storage_strategy.commit_transaction.side_effect = RuntimeError("db fail")
        uow = _ConcreteStrategyUoW([repo])
        uow.begin()
        with pytest.raises(TransactionError):
            uow.commit()


# ---------------------------------------------------------------------------
# StrategyUnitOfWork._rollback_transaction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStrategyUoWRollback:
    def test_storage_strategy_rollback_called(self) -> None:
        repo = _make_repo()
        uow = _ConcreteStrategyUoW([repo])
        uow.begin()
        uow.rollback()
        repo.storage_strategy.rollback_transaction.assert_called_once()

    def test_rollback_strategy_exception_warns_but_continues(self) -> None:
        """If one repo's rollback raises, the other repositories are still rolled back."""
        failing, healthy = _make_repo(), _make_repo()
        failing.storage_strategy.rollback_transaction.side_effect = RuntimeError("rollback err")
        uow = _ConcreteStrategyUoW([failing, healthy])
        uow.begin()
        uow.rollback()  # must not raise
        healthy.storage_strategy.rollback_transaction.assert_called_once()


# ---------------------------------------------------------------------------
# StrategyUnitOfWork — context manager integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStrategyUoWContextManager:
    def test_successful_block_commits(self) -> None:
        repo = _make_repo()
        uow = _ConcreteStrategyUoW([repo])
        with uow:
            pass
        repo.storage_strategy.commit_transaction.assert_called_once()

    def test_exception_triggers_rollback(self) -> None:
        repo = _make_repo()
        uow = _ConcreteStrategyUoW([repo])

        def _raise_inside_transaction():
            with uow:
                raise ValueError("fail")

        with pytest.raises(ValueError):
            _raise_inside_transaction()
        repo.storage_strategy.rollback_transaction.assert_called_once()

    def test_empty_repository_list_works(self) -> None:
        uow = _ConcreteStrategyUoW([])
        with uow:
            pass  # no repos — no crash


@pytest.mark.unit
class TestFailedCommitReleasesTransaction:
    def test_failed_commit_clears_in_transaction_without_implicit_rollback(self) -> None:
        from orb.infrastructure.storage.base.unit_of_work import BaseUnitOfWork

        class FailingCommit(BaseUnitOfWork):
            rolled_back = False
            machines = None
            requests = None
            templates = None

            def _begin_transaction(self) -> None:
                pass

            def _commit_transaction(self) -> None:
                raise RuntimeError("commit failed")

            def _rollback_transaction(self) -> None:
                self.rolled_back = True

        uow = FailingCommit()
        uow.begin()
        with pytest.raises(RuntimeError, match="commit failed"):
            uow.commit()

        assert uow.in_transaction is False
        assert uow.rolled_back is False
