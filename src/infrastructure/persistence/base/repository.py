"""Base repository interfaces and implementations."""

from typing import Any, Dict, Generic, List, Optional, TypeVar

from pydantic import ValidationError as PydanticValidationError

from domain.base.domain_interfaces import Repository
from domain.base.exceptions import ConcurrencyError, EntityNotFoundError

# Use lazy import for event_publisher to avoid circular imports
from infrastructure.logging.logger import get_logger

T = TypeVar("T")  # Entity type


class StrategyBasedRepository(Repository[T], Generic[T]):
    """Repository implementation using a storage strategy."""

    def __init__(self, entity_class: type, storage_strategy):
        """
        Initialize repository.

        Args:
            entity_class: Entity class
            storage_strategy: Storage strategy to use
        """
        self.entity_class = entity_class
        self.storage_strategy = storage_strategy
        self._cache: Dict[str, T] = {}
        self._version_map: Dict[str, int] = {}
        self.logger = get_logger(__name__)

    def _get_entity_id(self, entity: Any) -> str:
        """
        Get entity ID.

        Args:
            entity: Entity

        Returns:
            Entity ID
        """
        if hasattr(entity, "id"):
            return str(entity.id)
        elif hasattr(entity, "request_id"):
            return str(entity.request_id)
        elif hasattr(entity, "machine_id"):
            return str(entity.machine_id)
        else:
            raise ValueError(f"Cannot determine ID for entity: {entity}") from e

    def _to_dict(self, entity: Any) -> Dict[str, Any]:
        """
        Convert entity to dictionary.

        Args:
            entity: Entity

        Returns:
            Dictionary representation of entity
        """
        if hasattr(entity, "model_dump"):
            # Use Pydantic's serialization but process the result to handle value
            # objects
            data = entity.model_dump()
            # Lazy import to avoid circular dependency
            from infrastructure.utilities.common.serialization import (
                process_value_objects,
            )

            return process_value_objects(data)
        elif hasattr(entity, "to_dict"):
            # Process the result to handle value objects
            data = entity.to_dict()
            # Lazy import to avoid circular dependency
            from infrastructure.utilities.common.serialization import (
                process_value_objects,
            )

            return process_value_objects(data)
        else:
            # Process the result to handle value objects
            data = vars(entity)
            # Lazy import to avoid circular dependency
            from infrastructure.utilities.common.serialization import (
                process_value_objects,
            )

            return process_value_objects(data)

    def _from_dict(self, entity_dict: Dict[str, Any]) -> T:
        """
        Convert dictionary to entity.

        Args:
            entity_dict: Dictionary representation of entity

        Returns:
            Entity
        """
        try:
            if hasattr(self.entity_class, "model_validate"):
                return self.entity_class.model_validate(
                    entity_dict
                )  # Use Pydantic's deserialization
            elif hasattr(self.entity_class, "from_dict"):
                return self.entity_class.from_dict(entity_dict)  # Fallback
            else:
                return self.entity_class(**entity_dict)  # Last resort
        except PydanticValidationError as e:
            # Convert Pydantic validation error to ValueError
            raise ValueError(f"Validation error: {e}") from e

    def save(self, entity: Any) -> None:
        """
        Save entity and publish its events.

        Args:
            entity: Entity to save

        Raises:
            ValueError: If Pydantic validation fails
            ConcurrencyError: If entity version conflict
        """
        # Extract events from the entity if it has any
        events = []
        if hasattr(entity, "get_domain_events"):
            events = entity.get_domain_events()
        elif hasattr(entity, "events_list") and entity.events_list:
            # Backward compatibility
            events = entity.events_list.copy()

        try:
            # Get entity ID
            entity_id = self._get_entity_id(entity)

            # Check version if entity exists in cache
            if entity_id in self._version_map:
                # Access version through getattr to avoid type checking errors
                entity_version = getattr(entity, "version", None)
                if entity_version != self._version_map[entity_id]:
                    # Ensure entity_version is an int
                    raise ConcurrencyError(
                        self.entity_class.__name__,
                        entity_id,
                        self._version_map[entity_id],
                        int(entity_version) if entity_version is not None else 0,
                    )

            # Convert entity to dictionary
            entity_data = self._to_dict(entity)

            # Save entity
            self.storage_strategy.save(entity_id, entity_data)

            # Update cache
            self._cache[entity_id] = entity
            # Access version through getattr to avoid type checking errors
            entity_version = getattr(entity, "version", 0)
            self._version_map[entity_id] = entity_version + 1

            self.logger.debug(
                "Saved %s %s", self.entity_class.__name__, entity_id,
                extra={"entity_id": entity_id},
            )

            # Publish events after successful save
            if events:
                # Lazy import to avoid circular imports
                import asyncio

                from infrastructure.events import get_event_bus

                event_bus = get_event_bus()

                # Handle both new EventBus and legacy publisher
                if hasattr(event_bus, "publish") and asyncio.iscoroutinefunction(event_bus.publish):
                    # New EventBus (async)
                    for event in events:
                        try:
                            # Run async publish in sync context
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                # If we're already in an async context, create a task
                                asyncio.create_task(event_bus.publish(event))
                            else:
                                # If we're in sync context, run the coroutine
                                loop.run_until_complete(event_bus.publish(event))
                        except Exception:
                            # Fallback to sync publish if available
                            if hasattr(event_bus, "publish"):
                                try:
                                    event_bus.publish(event)
                                except Exception as sync_error:
                                    self.logger.error(
                                        "Failed to publish event %s via sync fallback: %s",  event.__class__.__name__, sync_error
                                    )
                                    # Event publishing failed completely - this is
                                    # serious for domain consistency
                else:
                    # Legacy publisher (sync)
                    for event in events:
                        event_bus.publish(event)

                # Clear events from entity if it has a clear_events method
                if hasattr(entity, "clear_domain_events"):
                    entity.clear_domain_events()
                    # Update cache with the entity that has events cleared
                    self._cache[entity_id] = entity
                elif hasattr(entity, "clear_events") and callable(entity.clear_events):
                    # Backward compatibility
                    updated_entity = entity.clear_events()
                    self._cache[entity_id] = updated_entity

                self.logger.debug(
                    "Published %s events for %s %s",  len(events),  self.entity_class.__name__, entity_id,
                    extra={"entity_id": entity_id},
                )
        except PydanticValidationError as e:
            # Convert Pydantic validation error to ValueError
            raise ValueError(f"Validation error: {e}") from e

    def find_by_id(self, entity_id: Any) -> Optional[T]:
        """
        Find entity by ID.

        Args:
            entity_id: Entity ID

        Returns:
            Entity if found, None otherwise
        """
        # Check cache first
        entity_id_str = str(entity_id)
        if entity_id_str in self._cache:
            return self._cache[entity_id_str]

        # Get entity data from storage
        entity_data = self.storage_strategy.find_by_id(entity_id_str)
        if entity_data is None:
            return None

        # Convert to entity
        entity = self._from_dict(entity_data)

        # Update cache
        self._cache[entity_id_str] = entity
        # Access version through getattr to avoid type checking errors
        entity_version = getattr(entity, "version", 0)
        self._version_map[entity_id_str] = entity_version

        return entity

    def find_all(self) -> List[Any]:
        """
        Find all entities.

        Returns:
            List of all entities
        """
        # Get all entities from storage
        entities_data = self.storage_strategy.find_all()

        # Convert to entities
        entities = []

        # Handle both dictionary and list return types from storage strategy
        if isinstance(entities_data, dict):
            for entity_id, entity_data in entities_data.items():
                # Use cached entity if available
                if entity_id in self._cache:
                    entities.append(self._cache[entity_id])
                else:
                    entity = self._from_dict(entity_data)
                    self._cache[entity_id] = entity
                    # Access version through getattr to avoid type checking errors
                    entity_version = getattr(entity, "version", 0)
                    self._version_map[entity_id] = entity_version
                    entities.append(entity)
        else:
            for entity_data in entities_data:
                entity_id = self._get_entity_id_from_dict(entity_data)
                # Use cached entity if available
                if entity_id in self._cache:
                    entities.append(self._cache[entity_id])
                else:
                    entity = self._from_dict(entity_data)
                    self._cache[entity_id] = entity
                    # Access version through getattr to avoid type checking errors
                    entity_version = getattr(entity, "version", 0)
                    self._version_map[entity_id] = entity_version
                    entities.append(entity)

        return entities

    def _get_entity_id_from_dict(self, data: Dict[str, Any]) -> str:
        """
        Get entity ID from dictionary.

        Args:
            data: Dictionary representation of entity

        Returns:
            Entity ID
        """
        if "id" in data:
            return str(data["id"])
        elif "request_id" in data:
            return str(data["request_id"])
        elif "machine_id" in data:
            return str(data["machine_id"])
        else:
            raise ValueError(f"Cannot determine ID for entity data: {data}") from e

    def delete(self, entity_id: Any) -> None:
        """
        Delete entity.

        Args:
            entity_id: Entity ID

        Raises:
            EntityNotFoundError: If entity not found
        """
        entity_id_str = str(entity_id)

        # Check if entity exists
        if not self.exists(entity_id_str):
            raise EntityNotFoundError(self.entity_class.__name__, entity_id_str) from e

        # Delete entity from storage
        self.storage_strategy.delete(entity_id_str)

        # Remove from cache
        if entity_id_str in self._cache:
            del self._cache[entity_id_str]
        if entity_id_str in self._version_map:
            del self._version_map[entity_id_str]

        self.logger.debug(
            "Deleted %s %s", self.entity_class.__name__, entity_id_str,
            extra={"entity_id": entity_id_str},
        )

    def exists(self, entity_id: Any) -> bool:
        """
        Check if entity exists.

        Args:
            entity_id: Entity ID

        Returns:
            True if entity exists, False otherwise
        """
        # Check cache first
        entity_id_str = str(entity_id)
        if entity_id_str in self._cache:
            return True

        # Check storage
        return self.storage_strategy.exists(entity_id_str)

    def find_by_criteria(self, criteria: Dict[str, Any]) -> List[Any]:
        """
        Find entities by criteria.

        Args:
            criteria: Dictionary of field-value pairs to match

        Returns:
            List of matching entities
        """
        # Get matching entities from storage
        entities_data = self.storage_strategy.find_by_criteria(criteria)

        # Convert to entities
        entities = []

        # Handle both dictionary and list return types from storage strategy
        if isinstance(entities_data, dict):
            for entity_id, entity_data in entities_data.items():
                # Use cached entity if available
                if entity_id in self._cache:
                    entities.append(self._cache[entity_id])
                else:
                    entity = self._from_dict(entity_data)
                    self._cache[entity_id] = entity
                    # Access version through getattr to avoid type checking errors
                    entity_version = getattr(entity, "version", 0)
                    self._version_map[entity_id] = entity_version
                    entities.append(entity)
        else:
            for entity_data in entities_data:
                entity_id = self._get_entity_id_from_dict(entity_data)
                # Use cached entity if available
                if entity_id in self._cache:
                    entities.append(self._cache[entity_id])
                else:
                    entity = self._from_dict(entity_data)
                    self._cache[entity_id] = entity
                    # Access version through getattr to avoid type checking errors
                    entity_version = getattr(entity, "version", 0)
                    self._version_map[entity_id] = entity_version
                    entities.append(entity)

        return entities

    def save_batch(self, entities: List[T]) -> None:
        """
        Save multiple entities in a single operation.

        Args:
            entities: List of entities to save

        Raises:
            ValueError: If Pydantic validation fails
            ConcurrencyError: If entity version conflict
        """
        try:
            # Prepare batch
            entity_batch = {}
            for entity in entities:
                # Get entity ID
                entity_id = self._get_entity_id(entity)

                # Check version if entity exists in cache
                if entity_id in self._version_map:
                    # Access version through getattr to avoid type checking errors
                    entity_version = getattr(entity, "version", 0)
                    if entity_version != self._version_map[entity_id]:
                        # Ensure entity_version is an int
                        raise ConcurrencyError(
                            self.entity_class.__name__,
                            entity_id,
                            self._version_map[entity_id],
                            int(entity_version) if entity_version is not None else 0,
                        )

                # Convert entity to dictionary
                entity_data = self._to_dict(entity)
                entity_batch[entity_id] = entity_data

                # Update cache
                self._cache[entity_id] = entity
                # Access version through getattr to avoid type checking errors
                entity_version = getattr(entity, "version", 0)
                self._version_map[entity_id] = entity_version + 1

            # Save batch
            if entity_batch:
                self.storage_strategy.save_batch(entity_batch)

                self.logger.debug(
                    "Saved batch of %s %s entities",  len(entity_batch),  self.entity_class.__name__
                )
        except PydanticValidationError as e:
            # Convert Pydantic validation error to ValueError
            raise ValueError(f"Validation error: {e}") from e

    def delete_batch(self, entity_ids: List[Any]) -> None:
        """
        Delete multiple entities in a single operation.

        Args:
            entity_ids: List of entity IDs to delete

        Raises:
            EntityNotFoundError: If any entity not found
        """
        # Convert entity IDs to strings
        entity_id_strs = [str(entity_id) for entity_id in entity_ids]

        # Check if entities exist
        for entity_id_str in entity_id_strs:
            if not self.exists(entity_id_str):
                raise EntityNotFoundError(self.entity_class.__name__, entity_id_str) from e

        # Delete entities from storage
        self.storage_strategy.delete_batch(entity_id_strs)

        # Remove from cache
        for entity_id_str in entity_id_strs:
            if entity_id_str in self._cache:
                del self._cache[entity_id_str]
            if entity_id_str in self._version_map:
                del self._version_map[entity_id_str]

        self.logger.debug(
            "Deleted batch of %s %s entities",  len(entity_id_strs),  self.entity_class.__name__
        )

    def clear_cache(self) -> None:
        """Clear the entity cache."""
        self._cache.clear()
        self._version_map.clear()
