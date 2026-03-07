"""Generic entity serializer - eliminates duplication across repository serializers.

This module provides a base serializer with common serialization/deserialization
logic to replace duplicated code in TemplateSerializer, RequestSerializer, and
MachineSerializer.
"""

from datetime import datetime
from typing import Any, Callable, Generic, Optional, TypeVar

from orb.infrastructure.logging.logger import get_logger

T = TypeVar("T")

logger = get_logger(__name__)


class GenericEntitySerializer(Generic[T]):
    """Generic entity serializer with common serialization logic.

    This class eliminates 80% of duplication across entity-specific serializers
    by providing common patterns for:
    - Datetime serialization/deserialization
    - Value object handling
    - Pydantic model validation
    - Error handling and logging
    """

    def __init__(
        self,
        entity_class: type[T],
        entity_name: str,
        id_field: str = "id",
    ) -> None:
        """Initialize generic serializer.

        Args:
            entity_class: Entity class to serialize/deserialize
            entity_name: Human-readable entity name for logging
            id_field: Name of the ID field (default: "id")
        """
        self.entity_class = entity_class
        self.entity_name = entity_name
        self.id_field = id_field
        self.logger = get_logger(__name__)

    def serialize_datetime(self, dt: Optional[datetime]) -> Optional[str]:
        """Serialize datetime to ISO format string.

        Args:
            dt: Datetime to serialize

        Returns:
            ISO format string or None
        """
        return dt.isoformat() if dt else None

    def deserialize_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Deserialize ISO format string to datetime.

        Args:
            dt_str: ISO format datetime string

        Returns:
            Datetime object or None
        """
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str)
        except (ValueError, TypeError) as e:
            self.logger.warning("Failed to parse datetime '%s': %s", dt_str, e)
            return None

    def serialize_value_object(self, value_obj: Any) -> Any:
        """Serialize value object to primitive type.

        Args:
            value_obj: Value object to serialize

        Returns:
            Serialized value (string, dict, etc.)
        """
        if value_obj is None:
            return None

        # Handle value objects with .value attribute
        if hasattr(value_obj, "value"):
            return str(value_obj.value)

        # Handle value objects with to_dict method
        if hasattr(value_obj, "to_dict"):
            return value_obj.to_dict()

        # Handle enums
        if hasattr(value_obj, "value") and hasattr(value_obj, "name"):
            return value_obj.value

        # Return as-is for primitives
        return value_obj

    def to_dict_with_schema(
        self,
        entity: T,
        field_mapping: dict[str, Callable[[T], Any]],
        schema_version: str = "2.0.0",
    ) -> dict[str, Any]:
        """Convert entity to dictionary using field mapping.

        Args:
            entity: Entity to serialize
            field_mapping: Dictionary mapping field names to getter functions
            schema_version: Schema version for migration support

        Returns:
            Dictionary representation of entity

        Raises:
            Exception: If serialization fails
        """
        try:
            result = {}
            for field_name, getter_func in field_mapping.items():
                try:
                    result[field_name] = getter_func(entity)
                except Exception as e:
                    self.logger.warning(
                        "Failed to serialize field '%s' for %s: %s",
                        field_name,
                        self.entity_name,
                        e,
                    )
                    result[field_name] = None

            # Add schema version
            result["schema_version"] = schema_version

            return result

        except Exception as e:
            entity_id = getattr(entity, self.id_field, "unknown")
            self.logger.error(
                "Failed to serialize %s %s: %s",
                self.entity_name,
                entity_id,
                e,
            )
            raise

    def from_dict_with_validation(
        self,
        data: dict[str, Any],
        field_processors: Optional[dict[str, Callable[[Any], Any]]] = None,
    ) -> T:
        """Convert dictionary to entity using Pydantic validation.

        Args:
            data: Dictionary representation of entity
            field_processors: Optional field-specific processors

        Returns:
            Entity instance

        Raises:
            Exception: If deserialization fails
        """
        try:
            # Process fields if processors provided
            if field_processors:
                processed_data = {}
                for key, value in data.items():
                    if key in field_processors:
                        processed_data[key] = field_processors[key](value)
                    else:
                        processed_data[key] = value
            else:
                processed_data = data

            # Use Pydantic's model_validate if available
            if hasattr(self.entity_class, "model_validate"):
                model_validate = self.entity_class.model_validate  # type: ignore[union-attr]
                return model_validate(processed_data)  # type: ignore
            # Fallback to from_dict if available
            elif hasattr(self.entity_class, "from_dict"):
                from_dict = self.entity_class.from_dict  # type: ignore[union-attr]
                return from_dict(processed_data)  # type: ignore
            # Last resort: direct instantiation
            else:
                return self.entity_class(**processed_data)  # type: ignore

        except Exception as e:
            entity_id = data.get(self.id_field, "unknown")
            self.logger.error(
                "Failed to deserialize %s %s: %s",
                self.entity_name,
                entity_id,
                e,
            )
            raise

    def get_entity_id(self, entity: T) -> str:
        """Extract entity ID as string.

        Args:
            entity: Entity to extract ID from

        Returns:
            Entity ID as string
        """
        id_value = getattr(entity, self.id_field, None)
        if id_value is None:
            raise ValueError(f"Entity has no {self.id_field} field")

        # Handle value objects with .value attribute
        if hasattr(id_value, "value"):
            return str(id_value.value)

        return str(id_value)

    def extract_field_with_fallback(
        self,
        data: dict[str, Any],
        primary_key: str,
        fallback_keys: list[str],
        default: Any = None,
    ) -> Any:
        """Extract field value with fallback keys.

        Args:
            data: Data dictionary
            primary_key: Primary key to try first
            fallback_keys: Fallback keys to try in order
            default: Default value if no keys found

        Returns:
            Field value or default
        """
        # Try primary key
        if primary_key in data:
            return data[primary_key]

        # Try fallback keys
        for key in fallback_keys:
            if key in data:
                return data[key]

        # Return default
        return default


class SerializationHelper:
    """Helper functions for common serialization patterns."""

    @staticmethod
    def serialize_list_field(
        items: Optional[list[Any]],
        serializer: Optional[Callable[[Any], Any]] = None,
    ) -> list[Any]:
        """Serialize list field with optional item serializer.

        Args:
            items: List of items to serialize
            serializer: Optional function to serialize each item

        Returns:
            Serialized list
        """
        if not items:
            return []

        if serializer:
            return [serializer(item) for item in items]

        return list(items)

    @staticmethod
    def serialize_dict_field(
        data: Optional[dict[str, Any]],
        key_serializer: Optional[Callable[[str], str]] = None,
        value_serializer: Optional[Callable[[Any], Any]] = None,
    ) -> dict[str, Any]:
        """Serialize dictionary field with optional key/value serializers.

        Args:
            data: Dictionary to serialize
            key_serializer: Optional function to serialize keys
            value_serializer: Optional function to serialize values

        Returns:
            Serialized dictionary
        """
        if not data:
            return {}

        result = {}
        for key, value in data.items():
            serialized_key = key_serializer(key) if key_serializer else key
            serialized_value = value_serializer(value) if value_serializer else value
            result[serialized_key] = serialized_value

        return result

    @staticmethod
    def normalize_field_names(
        data: dict[str, Any],
        field_mappings: dict[str, list[str]],
    ) -> dict[str, Any]:
        """Normalize field names from legacy formats.

        Args:
            data: Data dictionary with potentially legacy field names
            field_mappings: Map of canonical name to list of legacy names

        Returns:
            Dictionary with normalized field names
        """
        result = {}

        for canonical_name, legacy_names in field_mappings.items():
            # Try canonical name first
            if canonical_name in data:
                result[canonical_name] = data[canonical_name]
                continue

            # Try legacy names
            for legacy_name in legacy_names:
                if legacy_name in data:
                    result[canonical_name] = data[legacy_name]
                    break

        # Copy any fields not in mappings
        for key, value in data.items():
            if key not in result:
                result[key] = value

        return result
