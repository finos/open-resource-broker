"""Template repository implementation using configuration management."""

from typing import Any, Dict, List, Optional

from domain.base.ports import LoggingPort
from domain.template.aggregate import Template
from domain.template.repository import TemplateRepository
from infrastructure.template.configuration_manager import TemplateConfigurationManager


class TemplateRepositoryImpl(TemplateRepository):
    """Template repository implementation for configuration-based template management."""

    def __init__(self, template_manager: TemplateConfigurationManager, logger: LoggingPort) -> None:
        """Initialize repository with template configuration manager."""
        self._template_manager = template_manager
        self._logger = logger

    # Abstract methods from AggregateRepository
    def save(self, aggregate: Template) -> None:
        """Save a template aggregate."""
        self._logger.debug("Saving template: %s", aggregate.template_id)
        self._template_manager.save_template(aggregate)

    def find_by_id(self, aggregate_id: str) -> Optional[Template]:
        """Find template by aggregate ID (required by AggregateRepository)."""
        self._logger.debug("Finding template by ID: %s", aggregate_id)
        return self._template_manager.get_template(aggregate_id)

    def delete(self, aggregate_id: str) -> None:
        """Delete template by aggregate ID."""
        self._logger.debug("Deleting template: %s", aggregate_id)
        self._template_manager.delete_template(aggregate_id)

    # Abstract methods from TemplateRepository
    def find_by_template_id(self, template_id: str) -> Optional[Template]:
        """Find template by template ID (required by TemplateRepository)."""
        # Delegate to the main find_by_id method to avoid duplication
        return self.find_by_id(template_id)

    def find_by_provider_api(self, provider_api: str) -> List[Template]:
        """Find templates by provider API type."""
        self._logger.debug("Finding templates by provider API: %s", provider_api)
        return self._template_manager.get_templates_by_provider(provider_api)

    def find_active_templates(self) -> List[Template]:
        """Find all active templates."""
        self._logger.debug("Finding all active templates")
        return self._template_manager.get_all_templates()

    def search_templates(self, criteria: Dict[str, Any]) -> List[Template]:
        """Search templates by criteria."""
        self._logger.debug("Searching templates with criteria: %s", criteria)

        all_templates = self._template_manager.get_all_templates()

        filtered_templates = []
        for template in all_templates:
            matches = True

            for key, value in criteria.items():
                template_value = getattr(template, key, None)
                if template_value != value:
                    matches = False
                    break

            if matches:
                filtered_templates.append(template)

        return filtered_templates

    # Convenience methods
    def get_by_id(self, template_id: str) -> Optional[Template]:
        """Get template by ID (convenience method, delegates to find_by_id)."""
        return self.find_by_id(template_id)

    def get_all(self) -> List[Template]:
        """Get all templates."""
        return self.find_active_templates()

    def exists(self, template_id: str) -> bool:
        """Check if template exists."""
        return self._template_manager.get_template(template_id) is not None

    def validate_template(self, template: Template) -> List[str]:
        """Validate template configuration."""
        validation_result = self._template_manager.validate_template(template)
        return validation_result.errors if not validation_result.is_valid else []


def create_template_repository_impl(
    template_manager: TemplateConfigurationManager, logger: LoggingPort
) -> TemplateRepositoryImpl:
    """Create template repository implementation."""
    return TemplateRepositoryImpl(template_manager, logger)
