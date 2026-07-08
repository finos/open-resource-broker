"""Template repository interface - contract for template data access."""

from abc import abstractmethod
from typing import Any, Optional

from orb.domain.base.domain_interfaces import AggregateRepository

from .template_aggregate import Template


class TemplateRepository(AggregateRepository[Template]):  # type: ignore[type-var]
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

    def count_by_provider_api(self) -> dict[str, int]:
        """Return ``{provider_api: count}`` for all templates.

        Default implementation lists all templates and groups by provider_api.
        Concrete implementations backed by SQL should override this with a
        single ``SELECT provider_api, COUNT(*) GROUP BY provider_api`` query.
        """
        counts: dict[str, int] = {}
        for tmpl in self.find_all():
            key = str(getattr(tmpl, "provider_api", None) or "unknown").strip()
            counts[key] = counts.get(key, 0) + 1
        return counts
