"""DynamoDB storage strategy implementation using componentized architecture."""

from typing import Any, Callable, Optional

from botocore.exceptions import ClientError

from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.base.ports import LoggingPort
from orb.infrastructure.di.injectable import injectable
from orb.infrastructure.storage.base.strategy import BaseStorageStrategy

# Import components
from orb.infrastructure.storage.components import LockManager
from orb.infrastructure.storage.exceptions import StorageError
from orb.providers.aws.storage.components import (
    DynamoDBClientManager,
    DynamoDBConverter,
    DynamoDBTransactionManager,
)


@injectable
class DynamoDBStorageStrategy(BaseStorageStrategy):
    """
    DynamoDB storage strategy using componentized architecture.

    Orchestrates components for AWS client management, data conversion,
    and transaction management. Reduced from 908 lines to ~250 lines.

    Saves made with ``save_versioned`` / ``save_versioned_batch`` are optimistic:
    the item is written only while its stored ``version`` still equals the one
    the caller read, otherwise ``ConcurrencyError`` is raised. Inside a unit of
    work the condition is part of the staged transaction and a conflict surfaces
    when it commits.
    """

    supports_versioned_save = True

    def __init__(
        self,
        logger: LoggingPort,
        aws_client,
        region: Optional[str],
        table_name: str,
        profile: Optional[str] = None,
        transaction_manager: Optional[DynamoDBTransactionManager] = None,
    ) -> None:
        """
        Initialize DynamoDB storage strategy with components.

        Args:
            aws_client: AWS client instance
            region: AWS region
            table_name: DynamoDB table name
            profile: AWS profile name
            transaction_manager: Transaction manager shared with other strategies so
                that writes to several tables commit atomically. While it has an
                active transaction, writes are staged instead of applied and are
                not visible to reads (of this or any strategy) until it commits.
        """
        super().__init__()

        self.table_name = table_name
        self.region = region
        self.profile = profile
        self._logger = logger

        # Initialize components
        self.client_manager = DynamoDBClientManager(aws_client, region, profile)
        self.converter = DynamoDBConverter(partition_key="id")
        self.transaction_manager = transaction_manager or DynamoDBTransactionManager(
            self.client_manager
        )
        self.lock_manager = LockManager("simple")  # Simple lock for DynamoDB

        # Initialize table
        self._initialize_table()

        self._logger.debug("Initialized DynamoDB storage strategy for table %s", table_name)

    def is_healthy(self) -> tuple[bool, dict[str, Any]]:
        """Probe DynamoDB: confirm we can list tables AND the configured
        table exists with the expected key schema.

        Returns ``(healthy, details)``. Details are limited to the backend type and
        whether the configured table is present; table name and region are not
        included because the details are served by the health endpoint.
        """
        details: dict[str, Any] = {
            "type": "dynamodb",
        }
        try:
            if not self.client_manager.is_healthy():
                details["reason"] = "list_tables probe failed"
                return False, details
            # Confirm the configured table exists and inspect its schema.
            try:
                table_exists = self.client_manager.table_exists(self.table_name)
            except Exception as exc:
                self._logger.warning("DynamoDB table_exists check failed: %s", exc)
                details["reason"] = "table existence check failed"
                return False, details
            details["table_exists"] = table_exists
            if not table_exists:
                details["reason"] = "configured table does not exist"
                return False, details
            return True, details
        except Exception as exc:
            self._logger.warning("DynamoDB health probe failed: %s", exc)
            details["reason"] = "health probe failed"
            return False, details

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
                    self._logger.info("Created DynamoDB table: %s", self.table_name)
                else:
                    self._logger.warning("Failed to create DynamoDB table: %s", self.table_name)

        except Exception as e:
            self._logger.error("Failed to initialize table %s: %s", self.table_name, e)
            raise

    def save(self, entity_id: str, data: dict[str, Any]) -> None:
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

                if self.transaction_manager.is_active:
                    self.transaction_manager.add_put_item(
                        self.table_name, item, key=self.converter.get_key(entity_id)
                    )
                    return

                # Save to DynamoDB
                success = self.client_manager.put_item(self.table_name, item)

                if not success:
                    raise StorageError(f"Failed to save entity {entity_id}")

                self._logger.debug("Saved entity: %s", entity_id)

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Save")
                raise StorageError(f"Failed to save entity {entity_id}: {e}")
            except Exception as e:
                self._logger.error("Failed to save entity %s: %s", entity_id, e)
                raise StorageError(f"Failed to save entity {entity_id}: {e}")

    def defer_until_commit(self, callback: Callable[[], None]) -> None:
        """Run ``callback`` after the shared transaction commits; now if none is active."""
        self.transaction_manager.defer_until_commit(callback)

    def _version_condition(self, expected: int) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Condition expression, names and values requiring the stored version to be ``expected``.

        A missing or NULL ``version`` counts as 0, matching how repositories read
        unstamped items. The item must exist.
        """
        names = {"#pk": self.converter.partition_key, "#v": "version"}
        if expected > 0:
            return "attribute_exists(#pk) AND #v = :e", names, {":e": expected}
        return (
            "attribute_exists(#pk) AND "
            "(attribute_not_exists(#v) OR #v = :e OR attribute_type(#v, :nulltype))",
            names,
            {":e": 0, ":nulltype": "NULL"},
        )

    @staticmethod
    def _written_version(data: dict[str, Any], expected: Optional[int]) -> int:
        """Version a write stores: the entity's own, and always above the baseline."""
        local = data.get("version")
        local = local if isinstance(local, int) else 0
        return local if expected is None else max(local, expected + 1)

    def save_versioned(
        self, entity_id: str, data: dict[str, Any], expected_version: Optional[int]
    ) -> int:
        """Save one entity and return the version written.

        With ``expected_version`` the put is conditional on the stored version
        still being that value; an item that changed or disappeared raises
        ``ConcurrencyError``. Without it the put is unconditional and stores the
        entity's own version, which may lower a concurrent holder's baseline (that
        holder then gets a conflict and retries).
        """
        return self.save_versioned_batch([(entity_id, data, expected_version)])[0]

    def save_versioned_batch(
        self, items: list[tuple[str, dict[str, Any], Optional[int]]]
    ) -> list[int]:
        """Save several entities in one transaction; returns the versions written.

        Outside a unit of work the batch is its own atomic transaction (chunked,
        and then not atomic as a whole, above DynamoDB's per-call limit). Inside
        one the puts are staged with the rest of the unit of work.
        """
        if not items:
            return []
        with self.lock_manager.write_lock():
            try:
                versions = [self._written_version(data, exp) for _, data, exp in items]

                if self.transaction_manager.is_active:
                    self._stage_versioned(items, versions)
                    return versions

                if len(items) == 1:
                    entity_id, data, expected = items[0]
                    if expected is not None:
                        self._put_conditional(entity_id, data, expected, versions[0])
                        return versions
                    row = {**data, "version": versions[0]}
                    if not self.client_manager.put_item(
                        self.table_name, self.converter.to_dynamodb_item(entity_id, row)
                    ):
                        raise StorageError(f"Failed to save entity {entity_id}")
                    return versions

                with self.transaction_manager.atomic_operation():
                    self._stage_versioned(items, versions)
                return versions

            except (ConcurrencyError, StorageError):
                raise
            except ClientError as e:
                self.client_manager.handle_client_error(e, "Save")
                raise StorageError(f"Failed to save entities: {e}")
            except Exception as e:
                self._logger.error("Failed to save %s entities: %s", len(items), e)
                raise StorageError(f"Failed to save entities: {e}")

    def _stage_versioned(
        self, items: list[tuple[str, dict[str, Any], Optional[int]]], versions: list[int]
    ) -> None:
        for (entity_id, data, expected), written in zip(items, versions):
            item = self.converter.to_dynamodb_item(entity_id, {**data, "version": written})
            condition: Optional[str] = None
            names: Optional[dict[str, str]] = None
            values: Optional[dict[str, Any]] = None
            if expected is not None:
                condition, names, values = self._version_condition(expected)
            self.transaction_manager.add_put_item(
                self.table_name,
                item,
                condition_expression=condition,
                key=self.converter.get_key(entity_id),
                expression_attribute_names=names,
                expression_attribute_values=values,
                version_conditioned=expected is not None,
            )

    def _put_conditional(
        self, entity_id: str, data: dict[str, Any], expected: int, written: int
    ) -> None:
        """One conditional put outside a unit of work."""
        item = self.converter.to_dynamodb_item(entity_id, {**data, "version": written})
        condition, names, values = self._version_condition(expected)
        try:
            self.client_manager.put_item_conditional(
                self.table_name, item, condition, names, values
            )
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ConcurrencyError(
                    f"Concurrent write detected for entity '{entity_id}': the stored item is no "
                    f"longer at version {expected} (it was changed or deleted by another "
                    f"writer). Re-read the entity, re-apply your change, and retry.",
                    details={"entity_id": entity_id, "expected_version": expected},
                ) from None
            raise

    def find_by_id(self, entity_id: str) -> Optional[dict[str, Any]]:
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
                    self._logger.debug("Found entity: %s", entity_id)
                    return entity_data
                else:
                    self._logger.debug("Entity not found: %s", entity_id)
                    return None

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Find by ID")
                return None
            except Exception as e:
                self._logger.error("Failed to find entity %s: %s", entity_id, e)
                return None

    def find_all(self) -> dict[str, dict[str, Any]]:
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

                self._logger.debug("Loaded %s entities", len(entities))
                return entities

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Find all")
                return {}
            except Exception as e:
                self._logger.error("Failed to load all entities: %s", e)
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

                if self.transaction_manager.is_active:
                    self.transaction_manager.add_delete_item(self.table_name, key)
                    return

                # Delete from DynamoDB
                success = self.client_manager.delete_item(self.table_name, key)

                if success:
                    self._logger.debug("Deleted entity: %s", entity_id)
                else:
                    self._logger.warning("Entity not found for deletion: %s", entity_id)

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Delete")
                raise StorageError(f"Failed to delete entity {entity_id}: {e}")
            except Exception as e:
                self._logger.error("Failed to delete entity %s: %s", entity_id, e)
                raise StorageError(f"Failed to delete entity {entity_id}: {e}")

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

            self._logger.debug("Entity %s exists: %s", entity_id, exists)
            return exists

        except Exception as e:
            self._logger.error("Failed to check existence of entity %s: %s", entity_id, e)
            return False

    def find_by_criteria(self, criteria: dict[str, Any]) -> list[dict[str, Any]]:
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

                self._logger.debug("Found %s entities matching criteria", len(entities))
                return entities

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Find by criteria")
                raise StorageError(f"Failed to search entities: {e}") from e
            except Exception as e:
                self._logger.error("Failed to search entities: %s", e)
                raise StorageError(f"Failed to search entities: {e}") from e

    def save_batch(self, entities: dict[str, dict[str, Any]]) -> None:
        """
        Save multiple entities in batch.

        Args:
            entities: Dictionary of entities to save
        """
        with self.lock_manager.write_lock():
            try:
                if self.transaction_manager.is_active:
                    for entity_id, data in entities.items():
                        self.transaction_manager.add_put_item(
                            self.table_name,
                            self.converter.to_dynamodb_item(entity_id, data),
                            key=self.converter.get_key(entity_id),
                        )
                    return

                # Convert entities to DynamoDB items
                items = self.converter.prepare_batch_items(entities)

                # Batch write to DynamoDB
                success = self.client_manager.batch_write_items(self.table_name, items)

                if not success:
                    raise StorageError("Failed to save batch")

                self._logger.debug("Saved batch of %s entities", len(entities))

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Batch save")
                raise StorageError(f"Failed to save batch: {e}")
            except Exception as e:
                self._logger.error("Failed to save batch: %s", e)
                raise StorageError(f"Failed to save batch: {e}")

    def delete_batch(self, entity_ids: list[str]) -> None:
        """
        Delete multiple entities in batch.

        Args:
            entity_ids: List of entity IDs to delete
        """
        with self.lock_manager.write_lock():
            try:
                if self.transaction_manager.is_active:
                    for entity_id in entity_ids:
                        self.transaction_manager.add_delete_item(
                            self.table_name, self.converter.get_key(entity_id)
                        )
                    return

                # Outside a unit of work the batch is its own atomic transaction
                with self.transaction_manager.atomic_operation():
                    for entity_id in entity_ids:
                        key = self.converter.get_key(entity_id)
                        self.transaction_manager.add_delete_item(self.table_name, key)

                self._logger.debug("Deleted batch of %s entities", len(entity_ids))

            except ClientError as e:
                self.client_manager.handle_client_error(e, "Batch delete")
                raise StorageError(f"Failed to delete batch: {e}")
            except Exception as e:
                self._logger.error("Failed to delete batch: %s", e)
                raise StorageError(f"Failed to delete batch: {e}")

    def begin_transaction(self) -> None:
        """Begin transaction."""
        self.transaction_manager.begin_transaction()

    def commit_transaction(self) -> None:
        """Commit transaction."""
        self.transaction_manager.commit_transaction()

    def rollback_transaction(self) -> None:
        """Rollback transaction."""
        self.transaction_manager.rollback_transaction()

    def count(self) -> int:
        """Count total entities in the table."""
        try:
            items = self.client_manager.scan_table(self.table_name)
            return len(items)
        except Exception as e:
            self._logger.error("Failed to count entities: %s", e)
            return 0

    def cleanup(self) -> None:
        """Clean up resources."""
        # DynamoDB doesn't require explicit cleanup like file handles or connections
        self._logger.debug("Cleaned up DynamoDB storage strategy for %s", self.table_name)

    def get_table_name(self) -> str:
        """Get table name."""
        return self.table_name

    def get_client_manager(self) -> DynamoDBClientManager:
        """Get client manager for advanced operations."""
        return self.client_manager

    def get_transaction_manager(self) -> DynamoDBTransactionManager:
        """Get transaction manager for advanced operations."""
        return self.transaction_manager
