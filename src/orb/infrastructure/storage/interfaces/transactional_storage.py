"""Transactional storage interface for transaction operations."""

from abc import ABC, abstractmethod


class TransactionalStorage(ABC):
    """Interface for transactional storage operations."""

    @abstractmethod
    def begin_transaction(self) -> None:
        """Begin a transaction."""

    @abstractmethod
    def commit_transaction(self) -> None:
        """Commit the current transaction."""

    @abstractmethod
    def rollback_transaction(self) -> None:
        """Rollback the current transaction."""
