"""Infrastructure implementation of filter service."""

import re
from typing import Tuple

from domain.services.filter_service import FilterService, MachineFilter, FilterOperator


class MachineFilterService(FilterService):
    """Machine filtering service implementation."""
    
    def parse_filters(self, filter_expressions: list[str]) -> list[MachineFilter]:
        """Parse filter expressions to domain objects."""
        filters = []
        for expr in filter_expressions:
            try:
                field, operator, value = self._parse_single_filter(expr)
                filters.append(MachineFilter(field, operator, value))
            except ValueError as e:
                raise ValueError(f"Invalid filter '{expr}': {e}")
        return filters
    
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
