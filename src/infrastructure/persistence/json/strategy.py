"""JSON storage strategy implementation using componentized architecture."""

from typing import Any, Dict, List, Optional

from src.infrastructure.logging.logger import get_logger
from src.infrastructure.persistence.base.strategy import BaseStorageStrategy

# Import components
from src.infrastructure.persistence.components import (
    FileManager,
    JSONSerializer,
    LockManager,
    MemoryTransactionManager,
)
from src.infrastructure.persistence.exceptions import PersistenceError


class JSONStorageStrategy(BaseStorageStrategy):
    """
    JSON storage strategy using componentized architecture.

    Orchestrates components for file operations, locking, serialization,
    and transaction management. Reduced from 935 lines to ~200 lines.
    """

    def __init__(self, file_path: str, create_dirs: bool = True, entity_type: str = "entities"):
        """
        Initialize JSON storage strategy with components.

        Args:
            file_path: Path to JSON file
            create_dirs: Whether to create parent directories
            entity_type: Type of entities being stored (for logging)
        """
        super().__init__()

        self.entity_type = entity_type
        self.logger = get_logger(__name__)

        # Initialize components
        self.file_manager = FileManager(file_path, create_dirs)
        self.lock_manager = LockManager("reader_writer")
        self.serializer = JSONSerializer()
        self.transaction_manager = MemoryTransactionManager()

        # Cache for loaded data
        self._data_cache: Optional[Dict[str, Dict[str, Any]]] = None
        self._cache_valid = False

        self.logger.debug(
            f"Initialized JSON storage strategy for {entity_type} at {file_path}")

    def save(self, entity_id: str, data: Dict[str, Any]) -> None:
        """
        Save entity data to JSON file.

        Args:
            entity_id: Unique identifier for the entity
            data: Entity data to save
        """
        with self.lock_manager.write_lock():
            try:
                # Load current data
                all_data = self._load_data()

                # Update with new data
                all_data[entity_id] = data

                # Save atomically
                self._save_data(all_data)

                # Invalidate cache
                self._cache_valid = False

                self.logger.debug(f"Saved {self.entity_type} entity: {entity_id}")

            except Exception as e:
                self.logger.error(
                    f"Failed to save {self.entity_type} entity {entity_id}: {e}")
                raise PersistenceError(f"Failed to save entity {entity_id}: {e}")

    def find_by_id(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """
        Find entity by ID.

        Args:
            entity_id: Entity identifier

        Returns:
            Entity data if found, None otherwise
        """
        with self.lock_manager.read_lock():
            try:
                all_data = self._load_data()
                entity_data = all_data.get(entity_id)

                if entity_data:
                    self.logger.debug(f"Found {self.entity_type} entity: {entity_id}")
                else:
                    self.logger.debug(
                        f"{self.entity_type} entity not found: {entity_id}")

                return entity_data

            except Exception as e:
                self.logger.error(
                    f"Failed to find {self.entity_type} entity {entity_id}: {e}")
                raise PersistenceError(f"Failed to find entity {entity_id}: {e}")

    def find_all(self) -> Dict[str, Dict[str, Any]]:
        """
        Find all entities.

        Returns:
            Dictionary of all entities keyed by ID
        """
        with self.lock_manager.read_lock():
            try:
                all_data = self._load_data()
                self.logger.debug(f"Loaded {len(all_data)} {self.entity_type} entities")
                return all_data.copy()

            except Exception as e:
                self.logger.error(
                    f"Failed to load all {self.entity_type} entities: {e}")
                raise PersistenceError(f"Failed to load all entities: {e}")

    def delete(self, entity_id: str) -> None:
        """
        Delete entity by ID.

        Args:
            entity_id: Entity identifier
        """
        with self.lock_manager.write_lock():
            try:
                all_data = self._load_data()

                if entity_id not in all_data:
                    self.logger.warning(
                        f"{self.entity_type} entity not found for deletion: {entity_id}"
                    )
                    return

                # Remove entity
                del all_data[entity_id]

                # Save updated data
                self._save_data(all_data)

                # Invalidate cache
                self._cache_valid = False

                self.logger.debug(f"Deleted {self.entity_type} entity: {entity_id}")

            except Exception as e:
                self.logger.error(
                    f"Failed to delete {self.entity_type} entity {entity_id}: {e}")
                raise PersistenceError(f"Failed to delete entity {entity_id}: {e}")

    def exists(self, entity_id: str) -> bool:
        """
        Check if entity exists.

        Args:
            entity_id: Entity identifier

        Returns:
            True if entity exists, False otherwise
        """
        with self.lock_manager.read_lock():
            try:
                all_data = self._load_data()
                exists = entity_id in all_data
                self.logger.debug(
                    f"{self.entity_type} entity {entity_id} exists: {exists}")
                return exists

            except Exception as e:
                self.logger.error(
                    f"Failed to check existence of { self.entity_type} entity {entity_id}: {e}"
                )
                return False

    def find_by_criteria(self, criteria: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Find entities matching criteria.

        Args:
            criteria: Search criteria

        Returns:
            List of matching entities
        """
        with self.lock_manager.read_lock():
            try:
                all_data = self._load_data()
                matching_entities = []

                for entity_data in all_data.values():
                    if self._matches_criteria(entity_data, criteria):
                        matching_entities.append(entity_data)

                self.logger.debug(
                    f"Found { len(matching_entities)} { self.entity_type} entities matching criteria"
                )
                return matching_entities

            except Exception as e:
                self.logger.error(f"Failed to search {self.entity_type} entities: {e}")
                raise PersistenceError(f"Failed to search entities: {e}")

    def save_batch(self, entities: Dict[str, Dict[str, Any]]) -> None:
        """
        Save multiple entities in batch.

        Args:
            entities: Dictionary of entities to save
        """
        with self.lock_manager.write_lock():
            try:
                all_data = self._load_data()
                all_data.update(entities)
                self._save_data(all_data)
                self._cache_valid = False

                self.logger.debug(
                    f"Saved batch of {len(entities)} {self.entity_type} entities")

            except Exception as e:
                self.logger.error(
                    f"Failed to save batch of {self.entity_type} entities: {e}")
                raise PersistenceError(f"Failed to save batch: {e}")

    def delete_batch(self, entity_ids: List[str]) -> None:
        """
        Delete multiple entities in batch.

        Args:
            entity_ids: List of entity IDs to delete
        """
        with self.lock_manager.write_lock():
            try:
                all_data = self._load_data()

                for entity_id in entity_ids:
                    all_data.pop(entity_id, None)

                self._save_data(all_data)
                self._cache_valid = False

                self.logger.debug(
                    f"Deleted batch of {len(entity_ids)} {self.entity_type} entities")

            except Exception as e:
                self.logger.error(
                    f"Failed to delete batch of {self.entity_type} entities: {e}")
                raise PersistenceError(f"Failed to delete batch: {e}")

    def begin_transaction(self) -> None:
        """Begin transaction."""
        self.transaction_manager.begin_transaction()

    def commit_transaction(self) -> None:
        """Commit transaction."""
        self.transaction_manager.commit_transaction()

    def rollback_transaction(self) -> None:
        """Rollback transaction."""
        self.transaction_manager.rollback_transaction()

    def cleanup(self) -> None:
        """Clean up resources."""
        self._data_cache = None
        self._cache_valid = False
        self.logger.debug(f"Cleaned up JSON storage strategy for {self.entity_type}")

    def _load_data(self) -> Dict[str, Dict[str, Any]]:
        """Load data from file with caching."""
        if self._cache_valid and self._data_cache is not None:
            return self._data_cache

        try:
            content = self.file_manager.read_file()

            if not content.strip():
                data = {}
            else:
                data = self.serializer.deserialize(content)
                if not isinstance(data, dict):
                    self.logger.warning(
                        "Invalid data format in file, initializing empty data")
                    data = {}

            # Cache the data
            self._data_cache = data
            self._cache_valid = True

            return data

        except Exception as e:
            self.logger.error(f"Failed to load data: {e}")
            # Try to recover from backup
            if self.file_manager.recover_from_backup():
                self.logger.info("Recovered data from backup")
                return self._load_data()  # Recursive call after recovery
            else:
                self.logger.warning("No backup available, starting with empty data")
                return {}

    def _save_data(self, data: Dict[str, Dict[str, Any]]) -> None:
        """Save data to file with backup."""
        try:
            # Create backup before saving
            self.file_manager.create_backup()

            # Serialize and save
            content = self.serializer.serialize(data)
            self.file_manager.write_file(content)

            # Update cache
            self._data_cache = data
            self._cache_valid = True

        except Exception as e:
            self.logger.error(f"Failed to save data: {e}")
            raise

    def _matches_criteria(self, entity_data: Dict[str, Any], criteria: Dict[str, Any]) -> bool:
        """Check if entity matches search criteria."""
        for key, expected_value in criteria.items():
            if key not in entity_data:
                return False

            actual_value = entity_data[key]

            # Handle different comparison types
            if isinstance(expected_value, dict) and "$in" in expected_value:
                if actual_value not in expected_value["$in"]:
                    return False
            elif isinstance(expected_value, dict) and "$regex" in expected_value:
                import re

                pattern = expected_value["$regex"]
                if not re.search(pattern, str(actual_value)):
                    return False
            else:
                if actual_value != expected_value:
                    return False

        return True
