"""AWS Template Validation Service - Handles template validation operations."""

from typing import Any

from orb.domain.base.ports import LoggingPort
from orb.providers.base.strategy import ProviderOperation, ProviderResult


class AWSTemplateValidationService:
    """Service for AWS template validation operations."""

    def __init__(self, logger: LoggingPort):
        self._logger = logger

    def get_available_templates(self, operation: ProviderOperation) -> ProviderResult:
        """Handle available templates query operation."""
        try:
            templates = self._get_aws_templates()
            return ProviderResult.success_result(
                {"templates": templates, "count": len(templates)},
                {"operation": "get_available_templates"},
            )
        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to get available templates: {e}", "GET_TEMPLATES_ERROR"
            )

    def _get_aws_templates(self) -> list[dict[str, Any]]:
        """Get available AWS templates using scheduler strategy."""
        try:
            from orb.infrastructure.scheduler.registry import get_scheduler_registry

            scheduler_registry = get_scheduler_registry()
            scheduler_strategy = scheduler_registry.get_active_strategy()  # type: ignore[attr-defined]

            if scheduler_strategy:
                template_paths = scheduler_strategy.get_template_paths()
                templates = []
                for template_path in template_paths:
                    try:
                        template_data = scheduler_strategy.load_templates_from_path(template_path)
                        templates.extend(template_data)
                    except Exception as e:
                        self._logger.warning(
                            "Failed to load templates from %s: %s", template_path, e
                        )
                return templates
            else:
                self._logger.warning("No scheduler strategy available, using fallback templates")
                return self._get_fallback_templates()

        except Exception as e:
            self._logger.error("Failed to load templates via scheduler strategy: %s", e)
            return self._get_fallback_templates()

    def _get_fallback_templates(self) -> list[dict[str, Any]]:
        """Get fallback AWS templates when scheduler strategy is not available."""
        return [
            {
                "template_id": "aws-linux-basic",
                "name": "Amazon Linux 2 Basic",
                "image_id": "ami-0abcdef1234567890",
                "instance_type": "t3.micro",
                "description": "Basic Amazon Linux 2 instance",
            },
            {
                "template_id": "aws-ubuntu-basic",
                "name": "Ubuntu 20.04 Basic",
                "image_id": "ami-0fedcba0987654321",
                "instance_type": "t3.small",
                "description": "Basic Ubuntu 20.04 instance",
            },
        ]

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
            return ProviderResult.error_result(
                f"Failed to validate template: {e}", "VALIDATE_TEMPLATE_ERROR"
            )

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
            if not any(
                instance_type.startswith(prefix) for prefix in ["t3.", "t2.", "m5.", "c5.", "r5."]
            ):
                validation_warnings.append(f"Uncommon instance type: {instance_type}")

        return {
            "valid": len(validation_errors) == 0,
            "errors": validation_errors,
            "warnings": validation_warnings,
            "validated_fields": list(template_config.keys()),
        }
