"""Template configuration adapter implementing TemplateConfigurationPort."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from domain.base.ports.logging_port import LoggingPort
from domain.base.ports.template_configuration_port import TemplateConfigurationPort
from infrastructure.template.configuration_manager import TemplateConfigurationManager

# Use TYPE_CHECKING to avoid direct domain import
if TYPE_CHECKING:
    from domain.template.aggregate import Template


class TemplateConfigurationAdapter(TemplateConfigurationPort):
    """Adapter implementing TemplateConfigurationPort using centralized template configuration manager."""

    def __init__(self, template_manager: TemplateConfigurationManager, logger: LoggingPort):
        """
        Initialize adapter with template configuration manager and logger.

        Args:
            template_manager: Template configuration manager
            logger: Logging port for structured logging
        """
        self._template_manager = template_manager
        self._logger = logger

    def get_template_manager(self) -> Any:
        """Get template configuration manager."""
        return self._template_manager

    def load_templates(self) -> List["Template"]:
        """Load all templates from configuration."""
        return self._template_manager.get_all_templates_sync()

    def get_template_config(self, template_id: str) -> Optional[Dict[str, Any]]:
        """Get configuration for specific template."""
        template = self._template_manager.get_template(template_id)
        if template:
            return template.model_dump()
        return None

    def validate_template_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate template configuration and return errors."""
        errors = []

        # Basic validation
        if not config.get("template_id"):
            errors.append("Template ID is required")

        if not config.get("provider_api"):
            errors.append("Provider API is required")

        if not config.get("image_id"):
            errors.append("Image ID is required")

        # Use template manager for validation
        try:
            # Create a temporary template for validation
            from domain.template.aggregate import Template

            temp_template = Template(
                template_id=config.get("template_id", "temp"),
                image_id=config.get("image_id", ""),
                instance_type=config.get("instance_type", ""),
                subnet_ids=config.get("subnet_ids", []),
                security_group_ids=config.get("security_group_ids", []),
                price_type=config.get("price_type", "ondemand"),
                provider_api=config.get("provider_api", ""),
                metadata=config.get("metadata", {}),
            )

            # Use template manager's validation
            validation_result = self._template_manager.validate_template(temp_template)
            if not validation_result.is_valid:
                errors.extend(validation_result.errors)

        except Exception as e:
            # Don't fail validation if template validation fails
            self._logger.warning("Template validation failed: %s", e)
            errors.append(f"Template validation error: {str(e)}")

        return errors

    def _determine_provider_type(self, config: Dict[str, Any]) -> Optional[str]:
        """Determine provider type from configuration."""
        provider_api = config.get("provider_api", "")

        # Map provider APIs to provider types
        if provider_api in [
            "EC2Fleet",
            "SpotFleet",
            "RunInstances",
            "AutoScalingGroup",
        ]:
            return "aws"

        # Check for AWS-specific fields
        aws_fields = [
            "fleet_type",
            "allocation_strategy",
            "spot_fleet_request_expiry",
            "fleet_role",
        ]
        if any(field in config for field in aws_fields):
            return "aws"

        return None

    # Additional convenience methods for application layer

    def get_template_by_id(self, template_id: str) -> Optional["Template"]:
        """
        Get template by ID as domain object.

        Args:
            template_id: Template identifier

        Returns:
            Template domain object or None
        """
        return self._template_manager.get_template(template_id)

    def get_templates_by_provider(self, provider_api: str) -> List["Template"]:
        """
        Get templates by provider API as domain objects.

        Args:
            provider_api: Provider API identifier

        Returns:
            List of Template domain objects
        """
        all_templates = self._template_manager.get_all_templates()
        return [t for t in all_templates if getattr(t, "provider_api", None) == provider_api]

    def clear_cache(self) -> None:
        """Clear template cache."""
        if hasattr(self._template_manager, "clear_cache"):
            self._template_manager.clear_cache()
            if self._logger:
                self._logger.debug("Cleared template cache via adapter")

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        if hasattr(self._template_manager, "get_cache_stats"):
            return self._template_manager.get_cache_stats()
        return {"cache_type": "unknown", "cache_size": 0}


# Factory function for dependency injection
def create_template_configuration_adapter(
    template_manager: TemplateConfigurationManager, logger: Optional[LoggingPort] = None
) -> TemplateConfigurationAdapter:
    """
    Create TemplateConfigurationAdapter.

    Args:
        template_manager: Template configuration manager
        logger: Optional logger

    Returns:
        TemplateConfigurationAdapter instance
    """
    return TemplateConfigurationAdapter(template_manager, logger)
