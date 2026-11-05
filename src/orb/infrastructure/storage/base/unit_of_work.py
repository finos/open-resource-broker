"""Base unit of work interfaces and implementations."""

from abc import ABC, abstractmethod
from typing import Any, Protocol, TypeVar

from orb.domain.base.domain_interfaces import UnitOfWork
from orb.domain.base.exceptions import ConcurrencyError
from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.storage.exceptions import TransactionError

T = TypeVar("T")  # Repository type


class BaseUnitOfWork(UnitOfWork, ABC):
    """Base unit of work implementation."""

    def __init__(self) -> None:
        """Initialize unit of work."""
        self.logger = get_logger(__name__)
        self._in_transaction = False

    def __enter__(self) -> "BaseUnitOfWork":
        """Enter context manager."""
        self.begin()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager."""
        if exc_type is not None:
            self.logger.debug(
                "Rolling back transaction due to exception: %s",
                exc_val,
                exc_info=(exc_type, exc_val, exc_tb),
            )
            self.rollback()
        else:
            self.commit()

    @property
    def in_transaction(self) -> bool:
        """Check if unit of work is in a transaction."""
        return self._in_transaction

    def begin(self) -> None:
        """Begin transaction."""
        if self._in_transaction:
            raise TransactionError("Transaction already in progress")

        self._in_transaction = True
        self._begin_transaction()

        self.logger.debug("Transaction started")

    def commit(self) -> None:
        """Commit transaction."""
        if not self._in_transaction:
            raise TransactionError("No transaction in progress")

        # A failed commit ends the transaction as far as this object is
        # concerned; it is not rolled back implicitly and the unit of work must
        # not be reused afterwards.
        try:
            self._commit_transaction()
        finally:
            self._in_transaction = False

        self.logger.debug("Transaction committed")

    def rollback(self) -> None:
        """Rollback transaction."""
        if not self._in_transaction:
            raise TransactionError("No transaction in progress")

        try:
            self._rollback_transaction()
        finally:
            self._in_transaction = False

        self.logger.debug("Transaction rolled back")

    @abstractmethod
    def _begin_transaction(self) -> None:
        """Begin transaction implementation."""

    @abstractmethod
    def _commit_transaction(self) -> None:
        """Commit transaction implementation."""

    @abstractmethod
    def _rollback_transaction(self) -> None:
        """Rollback transaction implementation."""


class _StrategyBackedRepository(Protocol):
    """A repository that keeps its records in a storage strategy."""

    storage_strategy: Any


class StrategyUnitOfWork(BaseUnitOfWork):
    """Unit of work that begins, commits and rolls back its repositories' strategies."""

    def __init__(self, repositories: list[_StrategyBackedRepository]) -> None:
        """
        Initialize unit of work.

        Args:
            repositories: List of repositories to manage
        """
        super().__init__()
        self.repositories = repositories

    def _begin_transaction(self) -> None:
        """Begin transaction by delegating to storage strategies."""
        try:
            for repo in self.repositories:
                if hasattr(repo, "storage_strategy"):
                    repo.storage_strategy.begin_transaction()
        except Exception as e:
            self.logger.error("Error beginning transaction: %s", str(e))
            # Clean up any started transactions
            self._rollback_transaction()
            raise TransactionError(f"Error beginning transaction: {e!s}")

    def _commit_transaction(self) -> None:
        """Commit transaction by delegating to storage strategies."""
        try:
            for repo in self.repositories:
                if hasattr(repo, "storage_strategy"):
                    repo.storage_strategy.commit_transaction()
        except ConcurrencyError:
            # Callers retry on a conflict; wrapping it would hide it from them
            raise
        except Exception as e:
            self.logger.error("Error committing transaction: %s", str(e))
            raise TransactionError(f"Error committing transaction: {e!s}")

    def _rollback_transaction(self) -> None:
        """Rollback transaction by delegating to storage strategies."""
        for repo in self.repositories:
            if hasattr(repo, "storage_strategy"):
                try:
                    repo.storage_strategy.rollback_transaction()
                except Exception as e:
                    self.logger.warning("Error rolling back transaction for repository: %s", str(e))
