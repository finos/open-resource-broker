"""Template repository interface - contract for template data access."""

from abc import abstractmethod
from typing import Any, Optional

from domain.base.domain_interfaces import AggregateRepository

from .aggregate import Template


class TemplateRepository(AggregateRepository[Template]):
    """Repository interface for template aggregates."""

    @abstractmethod
    def find_by_template_id(self, template_id: str) -> Optional[Template]:
        """Find template by template ID."""

    @abstractmethod
    def find_by_provider_api(self, provider_api: str) -> list[Template]:
        """Find templates by provider API type."""

    @abstractmethod
    def find_active_templates(self) -> list[Template]:
        """Find all active templates."""

    @abstractmethod
    def search_templates(self, criteria: dict[str, Any]) -> list[Template]:
        """Search templates by criteria."""
