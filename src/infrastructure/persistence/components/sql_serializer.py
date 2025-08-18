"""SQL serialization components for domain to database mapping."""

import json
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from infrastructure.logging.logger import get_logger
from infrastructure.persistence.components.resource_manager import DataConverter


class SQLSerializer(DataConverter):
    """
    SQL serializer for converting between domain objects and database rows.

    Handles type conversion, JSON serialization for complex fields,
    and enum handling for SQL storage.
    """

    def __init__(self, id_column: str = "id"):
        """
        Initialize SQL serializer.

        Args:
            id_column: Name of the primary key column
        """
        self.id_column = id_column
        self.logger = get_logger(__name__)

    def to_storage_format(self, domain_data: Dict[str, Any]) -> Any:
        """Convert domain data to SQL format (implements DataConverter interface)."""
        # Extract entity_id from domain_data if present
        entity_id = domain_data.get(self.id_column, domain_data.get("id", "unknown"))
        return self.serialize_for_insert(entity_id, domain_data)

    def from_storage_format(self, storage_data: Any) -> Dict[str, Any]:
        """Convert SQL data to domain format (implements DataConverter interface)."""
        return self.deserialize_from_row(storage_data)

    def prepare_for_query(self, criteria: Dict[str, Any]) -> Any:
        """Prepare domain criteria for SQL query (implements DataConverter interface)."""
        return self.prepare_criteria(criteria)

    def serialize_for_insert(self, entity_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Serialize domain data for database INSERT.

        Args:
            entity_id: Entity identifier
            data: Domain data dictionary

        Returns:
            Database-ready data dictionary
        """
        try:
            serialized = {self.id_column: entity_id}

            for key, value in data.items():
                if key == self.id_column:
                    continue  # Skip ID as it's already set

                serialized[key] = self._serialize_value(value)

            # Add timestamps
            now = datetime.utcnow()
            if "created_at" not in serialized:
                serialized["created_at"] = now
            serialized["updated_at"] = now

            self.logger.debug("Serialized data for INSERT: %s", entity_id)
            return serialized

        except Exception as e:
            self.logger.error("Failed to serialize data for INSERT: %s", e)
            raise

    def serialize_for_update(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Serialize domain data for database UPDATE.

        Args:
            data: Domain data dictionary

        Returns:
            Database-ready data dictionary
        """
        try:
            serialized = {}

            for key, value in data.items():
                if key == self.id_column:
                    continue  # Skip ID for updates

                serialized[key] = self._serialize_value(value)

            # Update timestamp
            serialized["updated_at"] = datetime.utcnow()

            self.logger.debug("Serialized data for UPDATE")
            return serialized

        except Exception as e:
            self.logger.error("Failed to serialize data for UPDATE: %s", e)
            raise

    def deserialize_from_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deserialize database row to domain data.

        Args:
            row: Database row as dictionary

        Returns:
            Domain data dictionary
        """
        try:
            if not row:
                return {}

            deserialized = {}

            for key, value in row.items():
                deserialized[key] = self._deserialize_value(value)

            self.logger.debug("Deserialized row data")
            return deserialized

        except Exception as e:
            self.logger.error("Failed to deserialize row data: %s", e)
            raise

    def deserialize_from_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deserialize multiple database rows.

        Args:
            rows: List of database rows

        Returns:
            List of domain data dictionaries
        """
        try:
            return [self.deserialize_from_row(row) for row in rows]
        except Exception as e:
            self.logger.error("Failed to deserialize rows: %s", e)
            raise

    def _serialize_value(self, value: Any) -> Any:
        """
        Serialize individual value for database storage.

        Args:
            value: Value to serialize

        Returns:
            Database-compatible value
        """
        if value is None:
            return None

        # Handle enums
        if isinstance(value, Enum):
            return value.value

        # Handle datetime
        if isinstance(value, datetime):
            return value

        # Handle complex types (lists, dicts) as JSON
        if isinstance(value, (list, dict)):
            return json.dumps(value, default=str, ensure_ascii=False)

        # Handle boolean
        if isinstance(value, bool):
            return value

        # Handle numeric types
        if isinstance(value, (int, float)):
            return value

        # Handle strings
        if isinstance(value, str):
            return value

        # Default: convert to string
        return str(value)

    def _deserialize_value(self, value: Any) -> Any:
        """
        Deserialize individual value from database.

        Args:
            value: Database value

        Returns:
            Domain-compatible value
        """
        if value is None:
            return None

        # Handle JSON strings (try to parse as JSON)
        if isinstance(value, str):
            # Try to parse as JSON for complex types
            if value.startswith(("[", "{")):
                try:
                    return json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    # Not JSON, return as string
                    return value
            return value

        # Return other types as-is
        return value

    def get_entity_id(self, data: Dict[str, Any]) -> Optional[str]:
        """
        Extract entity ID from data.

        Args:
            data: Data dictionary

        Returns:
            Entity ID if found, None otherwise
        """
        return data.get(self.id_column)

    def prepare_criteria(self, criteria: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prepare search criteria for database query.

        Args:
            criteria: Search criteria

        Returns:
            Database-ready criteria
        """
        prepared = {}

        for key, value in criteria.items():
            if isinstance(value, Enum):
                prepared[key] = value.value
            elif isinstance(value, dict):
                # Handle special operators
                if "$in" in value:
                    prepared[key] = {
                        "$in": [v.value if isinstance(v, Enum) else v for v in value["$in"]]
                    }
                elif "$like" in value:
                    prepared[key] = {"$like": value["$like"]}
                else:
                    prepared[key] = value
            else:
                prepared[key] = value

        return prepared

    def serialize_batch(self, entities: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Serialize multiple entities for batch operations.

        Args:
            entities: Dictionary of entity_id -> data

        Returns:
            List of serialized data for batch insert
        """
        try:
            serialized_list = []

            for entity_id, data in entities.items():
                serialized = self.serialize_for_insert(entity_id, data)
                serialized_list.append(serialized)

            self.logger.debug("Serialized %s entities for batch operation", len(entities))
            return serialized_list

        except Exception as e:
            self.logger.error("Failed to serialize batch: %s", e)
            raise
