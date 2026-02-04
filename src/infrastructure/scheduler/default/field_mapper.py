"""Default scheduler field mapper - no transformations needed."""

from typing import Any, Dict

from infrastructure.scheduler.base.field_mapper import SchedulerFieldMapper


class DefaultFieldMapper(SchedulerFieldMapper):
    """Default scheduler field mapper - no transformations needed."""

    @property
    def field_mappings(self) -> Dict[str, str]:
        """No field mappings needed for default scheduler."""
        return {}

    def map_input_fields(self, external_template: Dict[str, Any]) -> Dict[str, Any]:
        """Identity mapping - no conversion needed."""
        return external_template

    def map_output_fields(self, internal_template: Dict[str, Any]) -> Dict[str, Any]:
        """Identity mapping - no conversion needed."""
        return internal_template

    def format_for_generation(self, internal_templates: list[dict]) -> list[dict]:
        """No conversion needed for default scheduler."""
        return internal_templates
