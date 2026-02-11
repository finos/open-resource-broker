"""Generic filter service for any object type using internal snake_case fields."""

import re
from dataclasses import dataclass
from typing import Any, Tuple

from domain.services.filter_service import FilterOperator


@dataclass(frozen=True)
class GenericFilter:
    """Generic filter for any object type."""
    field: str
    operator: FilterOperator
    value: str
    
    def matches(self, obj: dict) -> bool:
        """Apply filter to dictionary object."""
        field_value = self._get_nested_field(obj, self.field)
        return self.operator.apply(field_value, self.value)
    
    def _get_nested_field(self, obj: dict, field_path: str) -> Any:
        """Get nested field using dot notation with snake_case field names."""
        keys = field_path.split('.')
        current = obj
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            elif hasattr(current, key):
                current = getattr(current, key)
            else:
                return None
        return current


class GenericFilterService:
    """Generic filter service for any object type using internal snake_case fields."""
    
    def parse_filters(self, filter_expressions: list[str]) -> list[GenericFilter]:
        """Parse filter expressions to generic filter objects."""
        filters = []
        for expr in filter_expressions:
            try:
                field, operator, value = self._parse_single_filter(expr)
                filters.append(GenericFilter(field, operator, value))
            except ValueError as e:
                raise ValueError(f"Invalid filter '{expr}': {e}")
        return filters
    
    def apply_filters(self, objects: list[dict], filter_expressions: list[str]) -> list[dict]:
        """Apply filters to list of dictionary objects using snake_case field names."""
        if not filter_expressions:
            return objects
            
        filters = self.parse_filters(filter_expressions)
        return [obj for obj in objects if self._matches_all_filters(obj, filters)]
    
    def _matches_all_filters(self, obj: dict, filters: list[GenericFilter]) -> bool:
        """Check if object matches all filters (AND logic)."""
        return all(filter_obj.matches(obj) for filter_obj in filters)
    
    def _parse_single_filter(self, filter_expr: str) -> Tuple[str, FilterOperator, str]:
        """Parse single filter expression."""
        # Regex operator =~
        if "=~" in filter_expr:
            field, pattern = filter_expr.split("=~", 1)
            self._validate_regex(pattern.strip())
            return field.strip(), FilterOperator.REGEX, pattern.strip()
        
        # Negated regex !~
        if "!~" in filter_expr:
            field, pattern = filter_expr.split("!~", 1)
            self._validate_regex(pattern.strip())
            return field.strip(), FilterOperator.NOT_REGEX, pattern.strip()
        
        # Not equal !=
        if "!=" in filter_expr:
            field, value = filter_expr.split("!=", 1)
            return field.strip(), FilterOperator.NOT_EQUAL, value.strip()
        
        # Contains ~ (but not part of =~ or !~)
        if "~" in filter_expr and "=" not in filter_expr.split("~")[0]:
            field, value = filter_expr.split("~", 1)
            return field.strip(), FilterOperator.CONTAINS, value.strip()
        
        # Exact match =
        if "=" in filter_expr:
            field, value = filter_expr.split("=", 1)
            return field.strip(), FilterOperator.EXACT, value.strip()
        
        raise ValueError(f"Invalid filter format: {filter_expr}. Use field=value, field~value, field=~regex, etc.")
    
    def _validate_regex(self, pattern: str) -> None:
        """Validate regex pattern."""
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern '{pattern}': {e}")