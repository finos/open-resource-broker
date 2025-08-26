"""Helper functions for serializing and deserializing domain objects."""

from enum import Enum
from typing import Any, Optional, TypeVar

E = TypeVar("E", bound=Enum)


def serialize_enum(enum_value: Optional[Enum]) -> Optional[str]:
    """
    Serialize enum to string value.

    Args:
        enum_value: Enum value to serialize

    Returns:
        String representation of enum value, or None if enum_value is None
    """
    if enum_value is None:
        return None
    return enum_value.value if hasattr(enum_value, "value") else str(enum_value)


def deserialize_enum(enum_class: type[E], value: Any, default: Optional[E] = None) -> Optional[E]:
    """
    Deserialize string to enum value.

    Args:
        enum_class: Enum class to deserialize to
        value: Value to deserialize
        default: Default value if deserialization fails

    Returns:
        Enum value, or default if deserialization fails
    """
    if value is None:
        return default

    if isinstance(value, enum_class):
        return value

    try:
        if isinstance(value, str):
            return enum_class(value)
        return default
    except (ValueError, TypeError):
        return default


def process_value_objects(data: Any) -> Any:
    """
    Process value objects in data recursively.

    This function unwraps value objects in dictionaries, lists, and other
    nested structures to make them JSON serializable.

    Args:
        data: Data to process

    Returns:
        Processed data with value objects unwrapped
    """
    if isinstance(data, dict):
        return {k: process_value_objects(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [process_value_objects(item) for item in data]
    elif hasattr(data, "value") and not isinstance(data, (dict, list)):
        return data.value
    elif hasattr(data, "value") and isinstance(data, Enum):
        return data.value
    else:
        return data


# Alias for clarity in field serialization contexts
serialize_field = process_value_objects
