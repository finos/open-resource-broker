"""JSON encoders for serializing domain objects."""

import json
from datetime import datetime
from enum import Enum
from typing import Any


class ValueObjectEncoder(json.JSONEncoder):
    """
    JSON encoder that handles value objects, enums, datetime objects, and other non-serializable types.

    This encoder is used to serialize domain objects to JSON format. It handles:
    - Value objects with a .value property
    - Enum values
    - Datetime objects
    - Sets

    Usage:
        json.dumps(data, cls=ValueObjectEncoder)
    """

    def default(self, o: Any) -> Any:
        """
        Convert special objects to JSON serializable types.

        Args:
            o: Object to convert

        Returns:
            JSON serializable representation of the object
        """
        # Handle value objects with a .value property
        if hasattr(o, "value") and not isinstance(o, (dict, list)):
            return o.value
        # Handle enum values
        elif hasattr(o, "value") and isinstance(o, Enum):
            return o.value
        # Handle datetime objects
        elif isinstance(o, datetime):
            return o.isoformat()
        # Handle sets
        elif isinstance(o, set):
            return list(o)
        # Let the base class handle everything else
        return super().default(o)
