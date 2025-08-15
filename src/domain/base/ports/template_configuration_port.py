"""Template configuration port for application layer."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from domain.template.aggregate import Template


class TemplateConfigurationPort(ABC):
    """Port for template configuration operations."""

    @abstractmethod
    def get_template_manager(self) -> Any:
        """Get template configuration manager."""

    @abstractmethod
    def load_templates(self) -> List[Template]:
        """Load all templates from configuration."""

    @abstractmethod
    def get_template_config(self, template_id: str) -> Optional[Dict[str, Any]]:
        """Get configuration for specific template."""

    @abstractmethod
    def validate_template_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate template configuration and return errors."""
