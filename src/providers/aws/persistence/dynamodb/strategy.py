"""DynamoDB storage strategy implementation using componentized architecture."""

from typing import Any, Dict, List, Optional

from botocore.exceptions import ClientError

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from infrastructure.persistence.base.strategy import BaseStorageStrategy

# Import components
from infrastructure.persistence.components import (
    DynamoDBClientManager,
    DynamoDBConverter,
    DynamoDBTransactionManager,
    LockManager,
)
from infrastructure.persistence.exceptions import PersistenceError


@injectable
class DynamoDBStorageStrategy(BaseStorageStrategy):
    """
    DynamoDB storage strategy using componentized architecture.

    Orchestrates components for AWS client management, data conversion,
    and transaction management. Reduced from 908 lines to ~250 lines.
    """

    def __init__(
        self,
        logger: LoggingPort,
        aws_client,
        region: str,
        table_name: str,
        profile: Optional[str] = None,
    ):
        """
        Initialize DynamoDB storage strategy with components.

        Args:
            aws_client: AWS client instance
            region: AWS region
            table_name: DynamoDB table name
            profile: AWS profile name
        """
        super().__init__()

        self.table_name = table_name
        self.region = region
        self.profile = profile
        self._logger = logger

        # Initialize components
        self.client_manager = DynamoDBClientManager(aws_client, region, profile)
        self.converter = DynamoDBConverter(partition_key="id")
        self.transaction_manager = DynamoDBTransactionManager(self.client_manager)
        self.lock_manager = LockManager("simple")  # Simple lock for DynamoDB

        # Initialize table
        self._initialize_table()

        self._self._logger.debug("Initialized DynamoDB storage strategy for table %s", table_name)

    def _initialize_table(self) -> None:
        """Initialize DynamoDB table if it doesn't exist."""
        try:
            if not self.client_manager.table_exists(self.table_name):
                # Create table with basic schema
                # Partition key
                key_schema = [{"AttributeName": "id", "KeyType": "HASH"}]

                attribute_definitions = [{"AttributeName": "id", "AttributeType": "S"}]  # String

                success = self.client_manager.create_table(
                    self.table_name, key_schema, attribute_definitions
                )

                if success:
                    self._self._logger.info("Created DynamoDB table: %s", self.table_name)
                else:
                    self._self._logger.warning(
                        "Failed to create DynamoDB table: %s", self.table_name
                    )

        except Exception as e:
            self._self._logger.error("Failed to initialize table %s: %s", self.table_name, e)
            raise

    def save(self, entity_id: str, data: Dict[str, Any]) -> None:
        """
        Save entity data to DynamoDB table.

        Args:
            entity_id: Unique identifier for the entity
            data: Entity data to save
        """
        with self.lock_manager.write_lock():
            try:
                # Convert to DynamoDB item
                item = self.converter.to_dynamodb_item(entity_id, data)

                # Save to DynamoDB
                success = self.client_manager.put_item(self.table_name, item)

                if not success:
                    raise PersistenceError(f"Failed to save entity {entity_id}") from e

                self._self._logger.debug("Saved entity: %s", entity_id)

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Save")
                raise PersistenceError(f"Failed to save entity {entity_id}: {e}") from e
            except Exception as e:
                self._self._logger.error("Failed to save entity %s: %s", entity_id, e)
                raise PersistenceError(f"Failed to save entity {entity_id}: {e}") from e

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
                # Get key for DynamoDB
                key = self.converter.get_key(entity_id)

                # Get item from DynamoDB
                item = self.client_manager.get_item(self.table_name, key)

                if item:
                    entity_data = self.converter.from_dynamodb_item(item)
                    self._self._logger.debug("Found entity: %s", entity_id)
                    return entity_data
                else:
                    self._self._logger.debug("Entity not found: %s", entity_id)
                    return None

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Find by ID")
                return None
            except Exception as e:
                self._self._logger.error("Failed to find entity %s: %s", entity_id, e)
                return None

    def find_all(self) -> Dict[str, Dict[str, Any]]:
        """
        Find all entities.

        Returns:
            Dictionary of all entities keyed by ID
        """
        with self.lock_manager.read_lock():
            try:
                # Scan table for all items
                items = self.client_manager.scan_table(self.table_name)

                entities = {}
                for item in items:
                    entity_data = self.converter.from_dynamodb_item(item)
                    entity_id = self.converter.extract_entity_id(item)
                    if entity_id:
                        entities[entity_id] = entity_data

                self._self._logger.debug("Loaded %s entities", len(entities))
                return entities

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Find all")
                return {}
            except Exception as e:
                self._self._logger.error("Failed to load all entities: %s", e)
                return {}

    def delete(self, entity_id: str) -> None:
        """
        Delete entity by ID.

        Args:
            entity_id: Entity identifier
        """
        with self.lock_manager.write_lock():
            try:
                # Get key for DynamoDB
                key = self.converter.get_key(entity_id)

                # Delete from DynamoDB
                success = self.client_manager.delete_item(self.table_name, key)

                if success:
                    self._self._logger.debug("Deleted entity: %s", entity_id)
                else:
                    self._self._logger.warning("Entity not found for deletion: %s", entity_id)

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Delete")
                raise PersistenceError(f"Failed to delete entity {entity_id}: {e}") from e
            except Exception as e:
                self._self._logger.error("Failed to delete entity %s: %s", entity_id, e)
                raise PersistenceError(f"Failed to delete entity {entity_id}: {e}") from e

    def exists(self, entity_id: str) -> bool:
        """
        Check if entity exists.

        Args:
            entity_id: Entity identifier

        Returns:
            True if entity exists, False otherwise
        """
        try:
            # Get key for DynamoDB
            key = self.converter.get_key(entity_id)

            # Check if item exists
            item = self.client_manager.get_item(self.table_name, key)
            exists = item is not None

            self._self._logger.debug("Entity %s exists: %s", entity_id, exists)
            return exists

        except Exception as e:
            self._self._logger.error("Failed to check existence of entity %s: %s", entity_id, e)
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
                # Build filter expression
                filter_expression, expression_attribute_values = (
                    self.converter.build_filter_expression(criteria)
                )

                # Scan table with filter
                items = self.client_manager.scan_table(
                    self.table_name, filter_expression, expression_attribute_values
                )

                # Convert items to domain data
                entities = self.converter.from_dynamodb_items(items)

                self._self._logger.debug("Found %s entities matching criteria", len(entities))
                return entities

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Find by criteria")
                return []
            except Exception as e:
                self._self._logger.error("Failed to search entities: %s", e)
                return []

    def save_batch(self, entities: Dict[str, Dict[str, Any]]) -> None:
        """
        Save multiple entities in batch.

        Args:
            entities: Dictionary of entities to save
        """
        with self.lock_manager.write_lock():
            try:
                # Convert entities to DynamoDB items
                items = self.converter.prepare_batch_items(entities)

                # Batch write to DynamoDB
                success = self.client_manager.batch_write_items(self.table_name, items)

                if not success:
                    raise PersistenceError("Failed to save batch") from e

                self._self._logger.debug("Saved batch of %s entities", len(entities))

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Batch save")
                raise PersistenceError(f"Failed to save batch: {e}") from e
            except Exception as e:
                self._self._logger.error("Failed to save batch: %s", e)
                raise PersistenceError(f"Failed to save batch: {e}") from e

    def delete_batch(self, entity_ids: List[str]) -> None:
        """
        Delete multiple entities in batch.

        Args:
            entity_ids: List of entity IDs to delete
        """
        with self.lock_manager.write_lock():
            try:
                # Use transaction manager for batch delete
                with self.transaction_manager.atomic_operation():
                    for entity_id in entity_ids:
                        key = self.converter.get_key(entity_id)
                        self.transaction_manager.add_delete_item(self.table_name, key)

                self._self._logger.debug("Deleted batch of %s entities", len(entity_ids))

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Batch delete")
                raise PersistenceError(f"Failed to delete batch: {e}") from e
            except Exception as e:
                self._self._logger.error("Failed to delete batch: %s", e)
                raise PersistenceError(f"Failed to delete batch: {e}") from e

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
        # DynamoDB doesn't require explicit cleanup like file handles or connections
        self._self._logger.debug("Cleaned up DynamoDB storage strategy for %s", self.table_name)

    def get_table_name(self) -> str:
        """Get table name."""
        return self.table_name

    def get_client_manager(self) -> DynamoDBClientManager:
        """Get client manager for advanced operations."""
        return self.client_manager

    def get_transaction_manager(self) -> DynamoDBTransactionManager:
        """Get transaction manager for advanced operations."""
        return self.transaction_manager
