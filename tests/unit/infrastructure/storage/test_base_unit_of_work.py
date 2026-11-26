"""Unit tests for BaseUnitOfWork."""

from typing import Any

import pytest

from orb.infrastructure.storage.base.unit_of_work import BaseUnitOfWork
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
