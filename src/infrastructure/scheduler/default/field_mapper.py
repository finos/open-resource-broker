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
        """Convert HostFactory format to domain format for default scheduler."""
        converted_templates = []
        for template in internal_templates:
            # Convert HostFactory camelCase to domain snake_case
            converted = {
                "template_id": template.get("templateId"),
                "name": template.get("name"),
                "description": template.get("description"),
                "instance_type": template.get("vmType"),
                "image_id": template.get("imageId"),
                "max_instances": template.get("maxNumber", 1),
                "key_name": template.get("keyName"),
                "subnet_ids": template.get("subnetIds") or [template.get("subnetId")] if template.get("subnetId") else [],
                "security_group_ids": template.get("securityGroupIds", []),
                "price_type": template.get("priceType"),
                "max_price": template.get("maxSpotPrice"),
                "allocation_strategy": template.get("allocationStrategy"),
                "tags": template.get("instanceTags", {}),
                "provider_api": template.get("providerApi"),
                "created_at": template.get("createdAt"),
            }
            # Remove None values
            converted = {k: v for k, v in converted.items() if v is not None}
            converted_templates.append(converted)
        return converted_templates
