"""Template configuration adapter implementing TemplateConfigurationPort."""

from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.template_configuration_port import TemplateConfigurationPort
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager
from orb.infrastructure.template.dtos import TemplateDTO

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

        # Delegate to the active provider's registered validator so
        # provider-specific rules apply (e.g. the k8s validator rejects an
        # unknown provider_api or a bad namespace).  This runs on top of the
        # generic checks above; when no provider validator is available the
        # generic checks are the whole verdict.
        try:
            from orb.domain.template.template_aggregate import Template

            domain_template = Template(
                template_id=template_id or "temp",
                image_id=image_id or "",
                instance_type=config.get("instanceType") or config.get("instance_type", ""),
                subnet_ids=config.get("subnetIds") or config.get("subnet_ids", []),
                security_group_ids=config.get("securityGroupIds")
                or config.get("security_group_ids", []),
                price_type=config.get("priceType") or config.get("price_type", "ondemand"),
                provider_api=provider_api or "",
                provider_data=config.get("provider_data") or {},
                metadata=config.get("metadata", {}),
            )
            provider_type = (
                config.get("providerType")
                or config.get("provider_type")
                or self._PROVIDER_API_TO_TYPE.get(str(provider_api or ""))
            )
            errors.extend(self._provider_validation_errors(provider_type, domain_template))
        except Exception as e:
            self._logger.warning("Template validation failed: %s", e)
            errors.append(f"Template validation error: {e!s}")

        # De-duplicate while preserving order (generic + provider checks may
        # both flag e.g. a missing image).
        return list(dict.fromkeys(errors))

    # Map a template's provider_api to the registered provider-type key so the
    # correct provider validator factory is selected.
    _PROVIDER_API_TO_TYPE: dict[str, str] = {
        "Pod": "k8s",
        "Deployment": "k8s",
        "StatefulSet": "k8s",
        "Job": "k8s",
        "EC2Fleet": "aws",
        "SpotFleet": "aws",
        "ASG": "aws",
        "RunInstances": "aws",
    }

    def _provider_validation_errors(self, provider_type: Any, domain_template: Any) -> list[str]:
        """Run the active provider's registered validator, returning its errors.

        Best-effort: returns ``[]`` when no registry/validator is available or
        the provider type cannot be resolved, so the generic checks remain the
        verdict.  Handles both validator result shapes in use — the k8s
        validator returns an object with ``.errors``; other validators may
        return a dict with an ``errors`` key or a plain list of strings.
        """
        registry = getattr(self._template_manager, "_registry", None)
        if registry is None or not hasattr(registry, "create_validator"):
            return []

        if not provider_type:
            return []
        provider_type = str(provider_type)

        try:
            validator = registry.create_validator(provider_type)
        except Exception as e:  # pragma: no cover — defensive
            self._logger.debug("Could not create %s validator: %s", provider_type, e)
            return []
        if validator is None or not hasattr(validator, "validate"):
            return []

        try:
            result = validator.validate(domain_template)
        except Exception as e:  # pragma: no cover — defensive
            self._logger.debug("%s validator raised: %s", provider_type, e)
            return []

        raw_errors = getattr(result, "errors", None)
        if raw_errors is None and isinstance(result, dict):
            raw_errors = result.get("errors")
        if raw_errors is None and isinstance(result, list):
            raw_errors = result
        return [str(e) for e in (raw_errors or [])]

    # Additional convenience methods for application layer

    async def get_template_by_id(self, template_id: str) -> Optional[TemplateDTO]:
        """
        Get template by ID as TemplateDTO.

        Args:
            template_id: Template identifier

        Returns:
            TemplateDTO or None
        """
        return await self._template_manager.get_template_by_id(template_id)

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
        from orb.infrastructure.adapters.logging_adapter import LoggingAdapter

        logger = LoggingAdapter(__name__)
    return TemplateConfigurationAdapter(template_manager, logger)
