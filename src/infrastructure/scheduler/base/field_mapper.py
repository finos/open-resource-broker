"""Base field mapper for scheduler-specific field mapping and transformations."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class SchedulerFieldMapper(ABC):
    """Base class for scheduler-specific field mapping and transformations."""

    @property
    @abstractmethod
    def field_mappings(self) -> Dict[str, str]:
        """External field → Internal field mappings."""
        pass

    def map_input_fields(self, external_template: Dict[str, Any]) -> Dict[str, Any]:
        """Map external format → internal format (bidirectional)."""
        mapped = {}

        # Apply external → internal mappings
        for external_field, internal_field in self.field_mappings.items():
            if external_field in external_template:
                mapped[internal_field] = external_template[external_field]
            elif internal_field in external_template:  # Already internal format
                mapped[internal_field] = external_template[internal_field]

        # Copy unmapped fields
        for key, value in external_template.items():
            if key not in self.field_mappings and key not in mapped:
                mapped[key] = value

        return mapped

    def map_output_fields(self, internal_template: Dict[str, Any]) -> Dict[str, Any]:
        """Map internal format → external format + transformations."""
        # Apply internal → external mappings
        reverse_mappings = {v: k for k, v in self.field_mappings.items()}
        mapped = {}

        for internal_field, external_field in reverse_mappings.items():
            if internal_field in internal_template:
                mapped[external_field] = internal_template[internal_field]

        # Copy unmapped fields
        for key, value in internal_template.items():
            if key not in reverse_mappings:
                mapped[key] = value

        # Apply scheduler-specific transformations
        return self._apply_output_transformations(mapped)

    def format_for_generation(
        self, internal_templates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Format internal templates for scheduler's expected input format with transformations."""
        formatted = []

        for template in internal_templates:
            # Use the same logic as map_output_fields to get transformations
            formatted_template = self.map_output_fields(template)
            formatted.append(formatted_template)

        return formatted

    def _apply_output_transformations(self, mapped: Dict[str, Any]) -> Dict[str, Any]:
        """Apply scheduler-specific output transformations. Override in subclass."""
        return mapped
