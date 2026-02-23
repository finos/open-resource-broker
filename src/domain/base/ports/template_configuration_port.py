"""Template configuration port for application layer."""

from abc import ABC, abstractmethod
from typing import Any, Optional


class TemplateConfigurationPort(ABC):
    """Port for template configuration operations."""

    @abstractmethod
    def get_template_manager(self) -> Any:
        """Get template configuration manager."""

    @abstractmethod
    async def load_templates(self, provider_override: Optional[str] = None) -> list[Any]:
        """Load all templates from configuration."""

    @abstractmethod
    def get_template_config(self, template_id: str) -> Optional[dict[str, Any]]:
        """Get configuration for specific template."""

    @abstractmethod
    def validate_template_config(self, config: dict[str, Any]) -> list[str]:
        """Validate template configuration and return errors."""

    async def get_template_by_id(self, template_id: str) -> Optional[Any]:
        """Get a single template by ID. Override in implementations."""
        templates = await self.load_templates()
        for t in templates:
            tid = getattr(t, "template_id", None) or (
                t.get("template_id") if isinstance(t, dict) else None
            )
            if tid == template_id:
                return t
        return None

    async def get_templates_by_provider(self, provider_api: str) -> list[Any]:
        """Get templates filtered by provider API. Override in implementations."""
        templates = await self.load_templates()
        result = []
        for t in templates:
            papi = getattr(t, "provider_api", None) or (
                t.get("provider_api") if isinstance(t, dict) else None
            )
            if papi == provider_api:
                result.append(t)
        return result
