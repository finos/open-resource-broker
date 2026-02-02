"""AWS Template Validation Service - Handles template validation operations."""

from typing import Any

from domain.base.ports import LoggingPort
from providers.base.strategy import ProviderOperation, ProviderResult


class AWSTemplateValidationService:
    """Service for AWS template validation operations."""

    def __init__(self, logger: LoggingPort):
        self._logger = logger

    def validate_template(self, operation: ProviderOperation) -> ProviderResult:
        """Handle template validation operation."""
        try:
            template_config = operation.parameters.get("template_config", {})

            if not template_config:
                return ProviderResult.error_result(
                    "Template configuration is required for validation",
                    "MISSING_TEMPLATE_CONFIG",
                )

            validation_result = self._validate_aws_template(template_config)

            return ProviderResult.success_result(
                validation_result,
                {"operation": "validate_template"},
            )

        except Exception as e:
            return ProviderResult.error_result(f"Failed to validate template: {e}", "VALIDATE_TEMPLATE_ERROR")

    def _validate_aws_template(self, template_config: dict[str, Any]) -> dict[str, Any]:
        """Validate AWS-specific template configuration."""
        validation_errors = []
        validation_warnings = []

        # Required fields validation
        if "image_id" not in template_config:
            validation_errors.append("Missing required field: image_id")

        has_primary_type = "instance_type" in template_config
        has_multi_types = "instance_types" in template_config
        has_abis = "abis_instance_requirements" in template_config

        if not (has_primary_type or has_multi_types or has_abis):
            validation_errors.append(
                "Missing instance configuration: provide instance_type, instance_types, or abis_instance_requirements"
            )

        # AWS-specific validations
        if "image_id" in template_config:
            image_id = template_config["image_id"]
            if not image_id.startswith("ami-"):
                validation_errors.append(f"Invalid AMI ID format: {image_id}")

        if "instance_type" in template_config:
            instance_type = template_config["instance_type"]
            if not any(instance_type.startswith(prefix) for prefix in ["t3.", "t2.", "m5.", "c5.", "r5."]):
                validation_warnings.append(f"Uncommon instance type: {instance_type}")

        return {
            "valid": len(validation_errors) == 0,
            "errors": validation_errors,
            "warnings": validation_warnings,
            "validated_fields": list(template_config.keys()),
        }