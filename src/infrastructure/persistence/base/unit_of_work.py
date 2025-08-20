"""Base unit of work interfaces and implementations."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, TypeVar

from domain.base.domain_interfaces import UnitOfWork
from infrastructure.logging.logger import get_logger
from infrastructure.persistence.base.repository import StrategyBasedRepository
from infrastructure.persistence.exceptions import TransactionError

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

        self._commit_transaction()
        self._in_transaction = False

        self.logger.debug("Transaction committed")

    def rollback(self) -> None:
        """Rollback transaction."""
        if not self._in_transaction:
            raise TransactionError("No transaction in progress")

        self._rollback_transaction()
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


class StrategyUnitOfWork(BaseUnitOfWork):
    """Unit of work implementation for strategy-based repositories."""

    def __init__(self, repositories: List[StrategyBasedRepository]) -> None:
        """
        Initialize unit of work.

        Args:
            repositories: List of repositories to manage
        """
        super().__init__()
        self.repositories = repositories
        self._snapshots: Dict[StrategyBasedRepository, Dict[str, Any]] = {}

    def _begin_transaction(self) -> None:
        """Begin transaction by delegating to storage strategies."""
        try:
            # First take snapshots for backward compatibility
            for repo in self.repositories:
                self._snapshots[repo] = repo._cache.copy()

            # Then delegate to storage strategies
            for repo in self.repositories:
                if hasattr(repo, "storage_strategy"):
                    repo.storage_strategy.begin_transaction()
        except Exception as e:
            self.logger.error("Error beginning transaction: %s", str(e))
            # Clean up any started transactions
            self._rollback_transaction()
            raise TransactionError(f"Error beginning transaction: {str(e)}")

    def _commit_transaction(self) -> None:
        """Commit transaction by delegating to storage strategies."""
        try:
            # Delegate to storage strategies
            for repo in self.repositories:
                if hasattr(repo, "storage_strategy"):
                    repo.storage_strategy.commit_transaction()

            # Clear snapshots
            self._snapshots.clear()
        except Exception as e:
            self.logger.error("Error committing transaction: %s", str(e))
            raise TransactionError(f"Error committing transaction: {str(e)}")

    def _rollback_transaction(self) -> None:
        """Rollback transaction by delegating to storage strategies."""
        try:
            # Delegate to storage strategies
            for repo in self.repositories:
                if hasattr(repo, "storage_strategy"):
                    try:
                        repo.storage_strategy.rollback_transaction()
                    except Exception as e:
                        self.logger.warning(
                            "Error rolling back transaction for repository: %s", str(e)
                        )

            # Fall back to snapshots for backward compatibility
            for repo, snapshot in self._snapshots.items():
                repo._cache = snapshot.copy()
                # Reload version map
                repo._version_map = {
                    entity_id: entity.version for entity_id, entity in snapshot.items()
                }

            # Clear snapshots
            self._snapshots.clear()
        except Exception as e:
            self.logger.error("Error rolling back transaction: %s", str(e))
            raise TransactionError(f"Error rolling back transaction: {str(e)}")
