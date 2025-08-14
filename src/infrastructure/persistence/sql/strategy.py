"""SQL storage strategy implementation using componentized architecture."""

from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from src.infrastructure.logging.logger import get_logger
from src.infrastructure.persistence.base.strategy import BaseStorageStrategy

# Import components
from src.infrastructure.persistence.components import (
    LockManager,
    SQLConnectionManager,
    SQLQueryBuilder,
    SQLSerializer,
)
from src.infrastructure.persistence.exceptions import PersistenceError


class SQLStorageStrategy(BaseStorageStrategy):
    """
    SQL storage strategy using componentized architecture.

    Orchestrates components for database connections, query building,
    serialization, and locking. Reduced from 769 lines to ~200 lines.
    """

    def __init__(self, config: Dict[str, Any], table_name: str, columns: Dict[str, str]):
        """
        Initialize SQL storage strategy with components.

        Args:
            config: Database configuration
            table_name: Name of the database table
            columns: Column definitions (name -> type)
        """
        super().__init__()

        self.table_name = table_name
        self.columns = columns
        self.logger = get_logger(__name__)

        # Initialize components
        self.connection_manager = SQLConnectionManager(config)
        self.query_builder = SQLQueryBuilder(table_name, columns)
        self.serializer = SQLSerializer(id_column=self._get_id_column())
        self.lock_manager = LockManager("simple")  # Simple lock for SQL

        # Initialize database table
        self._initialize_table()

        self.logger.debug(f"Initialized SQL storage strategy for table {table_name}")

    def _get_id_column(self) -> str:
        """Get the primary key column name."""
        for column_name, column_type in self.columns.items():
            if "PRIMARY KEY" in column_type.upper():
                return column_name
        return "id"  # Default fallback

    def _initialize_table(self) -> None:
        """Initialize database table if it doesn't exist."""
        try:
            if not self.connection_manager.table_exists(self.table_name):
                create_table_sql = self.query_builder.build_create_table()
                self.connection_manager.execute_query(create_table_sql)
                self.logger.info(f"Created table: {self.table_name}")
        except Exception as e:
            self.logger.error(f"Failed to initialize table {self.table_name}: {e}")
            raise

    def save(self, entity_id: str, data: Dict[str, Any]) -> None:
        """
        Save entity data to SQL database.

        Args:
            entity_id: Unique identifier for the entity
            data: Entity data to save
        """
        with self.lock_manager.write_lock():
            try:
                # Check if entity exists
                if self.exists(entity_id):
                    # Update existing entity
                    serialized_data = self.serializer.serialize_for_update(data)
                    query, params = self.query_builder.build_update(
                        serialized_data, self._get_id_column(), entity_id
                    )
                else:
                    # Insert new entity
                    serialized_data = self.serializer.serialize_for_insert(entity_id, data)
                    query, params = self.query_builder.build_insert(serialized_data)

                with self.connection_manager.get_session() as session:
                    from sqlalchemy import text

                    session.execute(text(query), params)
                    session.commit()

                self.logger.debug(f"Saved entity: {entity_id}")

            except Exception as e:
                self.logger.error(f"Failed to save entity {entity_id}: {e}")
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
                query, param_name = self.query_builder.build_select_by_id(self._get_id_column())
                params = {param_name: entity_id}

                with self.connection_manager.get_session() as session:
                    result = session.execute(text(query), params)
                    row = result.fetchone()

                if row:
                    # Convert row to dictionary
                    row_dict = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
                    entity_data = self.serializer.deserialize_from_row(row_dict)
                    self.logger.debug(f"Found entity: {entity_id}")
                    return entity_data
                else:
                    self.logger.debug(f"Entity not found: {entity_id}")
                    return None

            except Exception as e:
                self.logger.error(f"Failed to find entity {entity_id}: {e}")
                raise PersistenceError(f"Failed to find entity {entity_id}: {e}")

    def find_all(self) -> Dict[str, Dict[str, Any]]:
        """
        Find all entities.

        Returns:
            Dictionary of all entities keyed by ID
        """
        with self.lock_manager.read_lock():
            try:
                query = self.query_builder.build_select_all()

                with self.connection_manager.get_session() as session:
                    result = session.execute(text(query))
                    rows = result.fetchall()

                entities = {}
                id_column = self._get_id_column()

                for row in rows:
                    row_dict = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
                    entity_data = self.serializer.deserialize_from_row(row_dict)
                    entity_id = entity_data.get(id_column)
                    if entity_id:
                        entities[str(entity_id)] = entity_data

                self.logger.debug(f"Loaded {len(entities)} entities")
                return entities

            except Exception as e:
                self.logger.error(f"Failed to load all entities: {e}")
                raise PersistenceError(f"Failed to load all entities: {e}")

    def delete(self, entity_id: str) -> None:
        """
        Delete entity by ID.

        Args:
            entity_id: Entity identifier
        """
        with self.lock_manager.write_lock():
            try:
                query, param_name = self.query_builder.build_delete(self._get_id_column())
                params = {param_name: entity_id}

                with self.connection_manager.get_session() as session:
                    result = session.execute(text(query), params)
                    session.commit()

                    if result.rowcount == 0:
                        self.logger.warning(f"Entity not found for deletion: {entity_id}")
                    else:
                        self.logger.debug(f"Deleted entity: {entity_id}")

            except Exception as e:
                self.logger.error(f"Failed to delete entity {entity_id}: {e}")
                raise PersistenceError(f"Failed to delete entity {entity_id}: {e}")

    def exists(self, entity_id: str) -> bool:
        """
        Check if entity exists.

        Args:
            entity_id: Entity identifier

        Returns:
            True if entity exists, False otherwise
        """
        try:
            query, param_name = self.query_builder.build_exists(self._get_id_column())
            params = {param_name: entity_id}

            with self.connection_manager.get_session() as session:
                result = session.execute(text(query), params)
                exists = result.fetchone() is not None

            self.logger.debug(f"Entity {entity_id} exists: {exists}")
            return exists

        except Exception as e:
            self.logger.error(f"Failed to check existence of entity {entity_id}: {e}")
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
                prepared_criteria = self.serializer.prepare_criteria(criteria)
                query, params = self.query_builder.build_select_by_criteria(prepared_criteria)

                with self.connection_manager.get_session() as session:
                    result = session.execute(text(query), params)
                    rows = result.fetchall()

                entities = []
                for row in rows:
                    row_dict = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
                    entity_data = self.serializer.deserialize_from_row(row_dict)
                    entities.append(entity_data)

                self.logger.debug(f"Found {len(entities)} entities matching criteria")
                return entities

            except Exception as e:
                self.logger.error(f"Failed to search entities: {e}")
                raise PersistenceError(f"Failed to search entities: {e}")

    def save_batch(self, entities: Dict[str, Dict[str, Any]]) -> None:
        """
        Save multiple entities in batch.

        Args:
            entities: Dictionary of entities to save
        """
        with self.lock_manager.write_lock():
            try:
                serialized_list = self.serializer.serialize_batch(entities)
                query, _ = self.query_builder.build_batch_insert(serialized_list)

                with self.connection_manager.get_session() as session:
                    for serialized_data in serialized_list:
                        session.execute(query, serialized_data)
                    session.commit()

                self.logger.debug(f"Saved batch of {len(entities)} entities")

            except Exception as e:
                self.logger.error(f"Failed to save batch: {e}")
                raise PersistenceError(f"Failed to save batch: {e}")

    def delete_batch(self, entity_ids: List[str]) -> None:
        """
        Delete multiple entities in batch.

        Args:
            entity_ids: List of entity IDs to delete
        """
        with self.lock_manager.write_lock():
            try:
                query, param_name = self.query_builder.build_delete(self._get_id_column())

                with self.connection_manager.get_session() as session:
                    for entity_id in entity_ids:
                        params = {param_name: entity_id}
                        session.execute(text(query), params)
                    session.commit()

                self.logger.debug(f"Deleted batch of {len(entity_ids)} entities")

            except Exception as e:
                self.logger.error(f"Failed to delete batch: {e}")
                raise PersistenceError(f"Failed to delete batch: {e}")

    def begin_transaction(self) -> None:
        """Begin transaction (handled by session)."""
        self.logger.debug("Transaction begin (handled by session)")

    def commit_transaction(self) -> None:
        """Commit transaction (handled by session)."""
        self.logger.debug("Transaction commit (handled by session)")

    def rollback_transaction(self) -> None:
        """Rollback transaction (handled by session)."""
        self.logger.debug("Transaction rollback (handled by session)")

    @contextmanager
    def transaction(self):
        """Context manager for database transactions."""
        with self.connection_manager.get_session() as session:
            try:
                yield session
                session.commit()
            except Exception as e:
                session.rollback()
                self.logger.error(f"Transaction failed: {e}")
                raise

    def cleanup(self) -> None:
        """Clean up resources."""
        self.connection_manager.close()
        self.logger.debug(f"Cleaned up SQL storage strategy for {self.table_name}")
