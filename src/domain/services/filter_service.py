"""Domain value objects and services for filtering."""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from fnmatch import fnmatch
from typing import Any


class FilterOperator(Enum):
    """Domain enum for filter operations."""

    EXACT = "="
    CONTAINS = "~"
    REGEX = "=~"
    NOT_REGEX = "!~"
    NOT_EQUAL = "!="

    def apply(self, field_value: Any, filter_value: str) -> bool:
        """Apply filter operation to field value."""
        if field_value is None:
            return False

        field_str = str(field_value).lower()
        filter_str = str(filter_value).lower()

        if self == FilterOperator.EXACT:
            if "*" in filter_str:
                return fnmatch(field_str, filter_str)
            return field_str == filter_str

        elif self == FilterOperator.CONTAINS:
            return filter_str in field_str

        elif self == FilterOperator.REGEX:
            try:
                return bool(re.search(filter_value, str(field_value), re.IGNORECASE))
            except re.error:
                return False

        elif self == FilterOperator.NOT_REGEX:
            try:
                return not bool(re.search(filter_value, str(field_value), re.IGNORECASE))
            except re.error:
                return False

        elif self == FilterOperator.NOT_EQUAL:
            if "*" in filter_str:
                return not fnmatch(field_str, filter_str)
            return field_str != filter_str

        return False


@dataclass(frozen=True)
class MachineFilter:
    """Domain value object for machine filtering."""

    field: str
    operator: FilterOperator
    value: str

    def matches(self, machine) -> bool:
        """Apply filter to machine."""
        field_value = self._get_field_value(machine)
        return self.operator.apply(field_value, self.value)

    def _get_field_value(self, machine) -> Any:
        """Get field value from machine object."""
        # Handle nested fields (e.g., metadata.key)
        if "." in self.field:
            current_attr = machine
            for part in self.field.split("."):
                current_attr = getattr(current_attr, part, None)
                if current_attr is None:
                    break
            return current_attr

        return getattr(machine, self.field, None)


class FilterService(ABC):
    """Domain service for filtering operations."""

    @abstractmethod
    def parse_filters(self, filter_expressions: list[str]) -> list[MachineFilter]:
        """Parse filter expressions to domain objects."""
        pass
