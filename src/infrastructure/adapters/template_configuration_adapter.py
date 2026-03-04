"""Template configuration adapter implementing TemplateConfigurationPort."""

from typing import TYPE_CHECKING, Any, Optional

from domain.base.ports.logging_port import LoggingPort
from domain.base.ports.template_configuration_port import TemplateConfigurationPort
from infrastructure.template.configuration_manager import TemplateConfigurationManager
from infrastructure.template.dtos import TemplateDTO

# Use TYPE_CHECKING to avoid direct domain import
if TYPE_CHECKING:
    pass


class TemplateConfigurationAdapter(TemplateConfigurationPort):
    """Adapter implementing TemplateConfigurationPort using centralized template configuration manager."""

    def __init__(self, template_manager: TemplateConfigurationManager, logger: LoggingPort) -> None:
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

    async def load_templates(self, provider_override: Optional[str] = None) -> list[Any]:
        """Load all templates from configuration."""
        return await self._template_manager.load_templates(provider_override=provider_override)

    def get_template_config(self, template_id: str) -> Optional[dict[str, Any]]:
        """Get configuration for specific template."""
        template = self._template_manager.get_template(template_id)
        if template:
            return template.model_dump()
        return None

    def validate_template_config(self, config: dict[str, Any]) -> list[str]:
        """Validate template configuration and return errors."""
        errors = []

        # Basic validation
        template_id = config.get("templateId") or config.get("template_id")
        if not template_id:
            errors.append("Template ID is required")

        provider_api = config.get("providerApi") or config.get("provider_api")
        if not provider_api:
            errors.append("Provider API is required")

        image_id = config.get("imageId") or config.get("image_id")
        if not image_id:
            errors.append("Image ID is required")

        # Use template manager for validation
        try:
            # Create a temporary template for validation
            from domain.template.template_aggregate import Template

            Template(
                template_id=template_id or "temp",
                image_id=image_id or "",
                instance_type=config.get("instanceType") or config.get("instance_type", ""),
                subnet_ids=config.get("subnetIds") or config.get("subnet_ids", []),
                security_group_ids=config.get("securityGroupIds")
                or config.get("security_group_ids", []),
                price_type=config.get("priceType") or config.get("price_type", "ondemand"),
                provider_api=provider_api or "",
                metadata=config.get("metadata", {}),
            )

            # Note: Template validation is skipped here as it requires async context.
            # Validation is performed by the template manager during template operations.
        except Exception as e:
            self._logger.warning("Template validation failed: %s", e)
            errors.append(f"Template validation error: {e!s}")

        return errors

    # Additional convenience methods for application layer

    async def get_template_by_id(self, template_id: str) -> Optional[TemplateDTO]:
        """
        Get template by ID as TemplateDTO.

        Args:
            template_id: Template identifier

        Returns:
            TemplateDTO or None
        """
        return self._template_manager.get_template(template_id)

    async def get_templates_by_provider(self, provider_api: str) -> list[TemplateDTO]:
        """
        Get templates by provider API as TemplateDTO objects.

        Args:
            provider_api: Provider API identifier

        Returns:
            List of TemplateDTO objects
        """
        all_templates = self._template_manager.get_all_templates_sync()
        return [t for t in all_templates if getattr(t, "provider_api", None) == provider_api]

    def clear_cache(self) -> None:
        """Clear template cache."""
        if hasattr(self._template_manager, "clear_cache"):
            self._template_manager.clear_cache()
            self._logger.debug("Cleared template cache via adapter")

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return {"cache_type": "unknown", "cache_size": 0}


# Factory function for dependency injection
def create_template_configuration_adapter(
    template_manager: TemplateConfigurationManager, logger: Optional[LoggingPort] = None
) -> "TemplateConfigurationAdapter":
    """
    Create TemplateConfigurationAdapter.

    Args:
        template_manager: Template configuration manager
        logger: Optional logger

    Returns:
        TemplateConfigurationAdapter instance
    """
    if logger is None:
        from infrastructure.adapters.logging_adapter import LoggingAdapter

        logger = LoggingAdapter(__name__)
    return TemplateConfigurationAdapter(template_manager, logger)
