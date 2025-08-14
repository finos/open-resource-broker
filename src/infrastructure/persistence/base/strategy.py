"""Storage strategy interfaces and base implementations."""

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union

from src.domain.base.ports.storage_port import StoragePort
from src.infrastructure.logging.logger import get_logger
from src.infrastructure.persistence.exceptions import PersistenceError

T = TypeVar("T")  # Entity type


class StorageStrategy(StoragePort[T], ABC, Generic[T]):
    """Interface for storage strategies implementing StoragePort."""

    @abstractmethod
    def cleanup(self) -> None:
        """
        Clean up resources used by the storage strategy.

        This method should be called when the storage strategy is no longer needed
        to ensure resource cleanup (connections, file handles, etc.).

        Raises:
            PersistenceError: If there's an error cleaning up resources
        """

    @abstractmethod
    def __enter__(self) -> "StorageStrategy[T]":
        """
        Enter context manager.

        Returns:
            Self for use in with statement
        """

    @abstractmethod
    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> bool:
        """
        Exit context manager.

        Args:
            exc_type: Exception type if an exception was raised in the with block, None otherwise
            exc_val: Exception value if an exception was raised in the with block, None otherwise
            exc_tb: Exception traceback if an exception was raised in the with block, None otherwise

        Returns:
            True if the exception was handled, False otherwise
        """

    @abstractmethod
    def save(self, entity_id: str, data: Dict[str, Any]) -> None:
        """
        Save entity data.

        Args:
            entity_id: Entity ID
            data: Entity data

        Raises:
            PersistenceError: If there's an error saving the entity
        """

    @abstractmethod
    def find_by_id(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """
        Find entity by ID.

        Args:
            entity_id: Entity ID

        Returns:
            Entity data if found, None otherwise
        """

    @abstractmethod
    def find_all(self) -> Union[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        """
        Find all entities.

        Returns:
            List of entity data or dictionary of entity ID to entity data
        """

    @abstractmethod
    def delete(self, entity_id: str) -> None:
        """
        Delete entity.

        Args:
            entity_id: Entity ID

        Raises:
            PersistenceError: If there's an error deleting the entity
        """

    @abstractmethod
    def exists(self, entity_id: str) -> bool:
        """
        Check if entity exists.

        Args:
            entity_id: Entity ID

        Returns:
            True if entity exists, False otherwise
        """

    @abstractmethod
    def find_by_criteria(self, criteria: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Find entities by criteria.

        Args:
            criteria: Dictionary of field-value pairs to match

        Returns:
            List of matching entity data
        """

    @abstractmethod
    def begin_transaction(self) -> None:
        """
        Begin a transaction.

        Raises:
            PersistenceError: If there's an error beginning the transaction
        """

    @abstractmethod
    def commit_transaction(self) -> None:
        """
        Commit the current transaction.

        Raises:
            PersistenceError: If there's an error committing the transaction
        """

    @abstractmethod
    def rollback_transaction(self) -> None:
        """
        Rollback the current transaction.

        Raises:
            PersistenceError: If there's an error rolling back the transaction
        """

    @abstractmethod
    def save_batch(self, entities: Dict[str, Dict[str, Any]]) -> None:
        """
        Save multiple entities in a single operation.

        Args:
            entities: Dictionary of entity ID to entity data

        Raises:
            PersistenceError: If there's an error saving the entities
        """

    @abstractmethod
    def delete_batch(self, entity_ids: List[str]) -> None:
        """
        Delete multiple entities in a single operation.

        Args:
            entity_ids: List of entity IDs to delete

        Raises:
            PersistenceError: If there's an error deleting the entities
        """


class BaseStorageStrategy(StorageStrategy[T], Generic[T]):
    """Base storage strategy implementation with common functionality."""

    def __init__(self) -> None:
        """Initialize base storage strategy."""
        super().__init__()
        self._in_transaction = False
        self._transaction_snapshot = None
        self.logger = get_logger(__name__)
        self._is_closed = False

    def cleanup(self) -> None:
        """
        Clean up resources used by the storage strategy.

        This is a base implementation that should be overridden by subclasses
        to perform specific cleanup operations.

        Raises:
            PersistenceError: If there's an error cleaning up resources
        """
        try:
            # Rollback any active transaction
            if self._in_transaction:
                self.rollback_transaction()

            # Mark as closed
            self._is_closed = True

            self.logger.debug("Base storage strategy cleanup completed")
        except Exception as e:
            error_msg = f"Error cleaning up storage strategy: {str(e)}"
            self.logger.error(error_msg)
            raise PersistenceError(error_msg) from e

    def __enter__(self) -> "StorageStrategy[T]":
        """
        Enter context manager.

        Returns:
            Self for use in with statement
        """
        if self._is_closed:
            raise PersistenceError("Cannot enter context with closed storage strategy")
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> bool:
        """
        Exit context manager.

        Performs cleanup operations when exiting the context.

        Args:
            exc_type: Exception type if an exception was raised in the with block, None otherwise
            exc_val: Exception value if an exception was raised in the with block, None otherwise
            exc_tb: Exception traceback if an exception was raised in the with block, None otherwise

        Returns:
            False to propagate exceptions
        """
        try:
            # Rollback transaction if an exception occurred
            if exc_type is not None and self._in_transaction:
                self.logger.warning(
                    f"Exception in context manager, rolling back transaction: {exc_val}"
                )
                self.rollback_transaction()

            # Clean up resources
            self.cleanup()

            # Don't suppress exceptions
            return False
        except Exception as e:
            self.logger.error(f"Error in context manager exit: {str(e)}")
            # Don't suppress the original exception
            return False

    def begin_transaction(self) -> None:
        """
        Begin a transaction.

        Raises:
            PersistenceError: If there's an error beginning the transaction
        """
        if self._in_transaction:
            raise PersistenceError("Transaction already in progress")

        try:
            # Take a snapshot of the current state
            all_entities = self.find_all()
            if isinstance(all_entities, dict):
                self._transaction_snapshot = all_entities.copy()
            else:
                # Convert list to dictionary by entity ID
                self._transaction_snapshot = {}
                for entity in all_entities:
                    entity_id = self._get_entity_id_from_dict(entity)
                    self._transaction_snapshot[entity_id] = entity.copy()

            self._in_transaction = True
        except Exception as e:
            raise PersistenceError(f"Error beginning transaction: {str(e)}") from e

    def commit_transaction(self) -> None:
        """
        Commit the current transaction.

        Raises:
            PersistenceError: If there's an error committing the transaction
        """
        if not self._in_transaction:
            raise PersistenceError("No transaction in progress")

        try:
            # Clear the snapshot
            self._transaction_snapshot = None
            self._in_transaction = False
        except Exception as e:
            raise PersistenceError(f"Error committing transaction: {str(e)}") from e

    def rollback_transaction(self) -> None:
        """
        Rollback the current transaction.

        Raises:
            PersistenceError: If there's an error rolling back the transaction
        """
        if not self._in_transaction:
            raise PersistenceError("No transaction in progress")

        try:
            # Restore from snapshot
            if self._transaction_snapshot:
                # Subclasses should override this method to implement snapshot
                # restoration
                pass

            self._transaction_snapshot = None
            self._in_transaction = False
        except Exception as e:
            raise PersistenceError(f"Error rolling back transaction: {str(e)}") from e

    def save_batch(self, entities: Dict[str, Dict[str, Any]]) -> None:
        """
        Save multiple entities in a single operation.

        Args:
            entities: Dictionary of entity ID to entity data

        Raises:
            PersistenceError: If there's an error saving the entities
        """
        try:
            # Default implementation saves entities one by one
            for entity_id, entity_data in entities.items():
                self.save(entity_id, entity_data)
        except Exception as e:
            raise PersistenceError(f"Error saving batch: {str(e)}") from e

    def delete_batch(self, entity_ids: List[str]) -> None:
        """
        Delete multiple entities in a single operation.

        Args:
            entity_ids: List of entity IDs to delete

        Raises:
            PersistenceError: If there's an error deleting the entities
        """
        try:
            # Default implementation deletes entities one by one
            for entity_id in entity_ids:
                self.delete(entity_id)
        except Exception as e:
            raise PersistenceError(f"Error deleting batch: {str(e)}") from e

    def _get_entity_id_from_dict(self, data: Dict[str, Any]) -> str:
        """
        Get entity ID from dictionary.

        Args:
            data: Dictionary representation of entity

        Returns:
            Entity ID

        Raises:
            ValueError: If entity ID cannot be determined
        """
        if "id" in data:
            return str(data["id"])
        elif "request_id" in data:
            return str(data["request_id"])
        elif "machine_id" in data:
            return str(data["machine_id"])
        elif "template_id" in data:
            return str(data["template_id"])
        else:
            raise ValueError(f"Cannot determine ID for entity data: {data}")

    def find_by_criteria(self, criteria: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Find entities by criteria.

        Args:
            criteria: Dictionary of field-value pairs to match

        Returns:
            List of matching entity data
        """
        # Default implementation that loads all entities and filters in memory
        all_entities = self.find_all()
        matching_entities = []

        # Handle both dictionary and list return types
        if isinstance(all_entities, dict):
            entities_list = list(all_entities.values())
        else:
            entities_list = all_entities

        # Filter entities by criteria
        for entity_data in entities_list:
            if self._matches_criteria(entity_data, criteria):
                matching_entities.append(entity_data)

        return matching_entities

    def _matches_criteria(self, entity_data: Dict[str, Any], criteria: Dict[str, Any]) -> bool:
        """
        Check if entity matches criteria.

        Args:
            entity_data: Entity data
            criteria: Dictionary of field-value pairs to match

        Returns:
            True if entity matches criteria, False otherwise
        """
        for field, value in criteria.items():
            # Handle nested fields with dot notation
            if "." in field:
                parts = field.split(".")
                current = entity_data
                for part in parts[:-1]:
                    if part not in current:
                        return False
                    current = current[part]

                if parts[-1] not in current or current[parts[-1]] != value:
                    return False
            # Handle list fields
            elif isinstance(entity_data.get(field), list) and not isinstance(value, list):
                if value not in entity_data.get(field, []):
                    return False
            # Handle regular fields
            elif field not in entity_data or entity_data[field] != value:
                return False

        return True
