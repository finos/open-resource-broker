"""Template repository implementation using configuration management."""

import asyncio
from typing import Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.domain.template.repository import TemplateRepository
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager
from orb.infrastructure.template.dtos import TemplateDTO


def _dto_to_template(dto: TemplateDTO) -> Template:
    """Convert a TemplateDTO to a Template domain object."""
    return Template(
        template_id=dto.template_id,
        name=dto.name,
        provider_api=dto.provider_api,
    )


def _run_async(coro):
    """Run a coroutine synchronously."""
    try:
        asyncio.get_running_loop()
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


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
        dto = TemplateDTO.from_domain(aggregate)
        _run_async(self._template_manager.save_template(dto))

    def find_by_id(self, aggregate_id: str) -> Optional[Template]:
        """Find template by aggregate ID (required by AggregateRepository)."""
        self._logger.debug("Finding template by ID: %s", aggregate_id)
        dto = self._template_manager.get_template(aggregate_id)
        return _dto_to_template(dto) if dto is not None else None

    def delete(self, aggregate_id: str) -> None:
        """Delete template by aggregate ID."""
        self._logger.debug("Deleting template: %s", aggregate_id)
        _run_async(self._template_manager.delete_template(aggregate_id))

    # Abstract methods from TemplateRepository
    def find_by_template_id(self, template_id: str) -> Optional[Template]:
        """Find template by template ID (required by TemplateRepository)."""
        return self.find_by_id(template_id)

    def find_by_provider_api(self, provider_api: str) -> list[Template]:
        """Find templates by provider API type."""
        self._logger.debug("Finding templates by provider API: %s", provider_api)
        dtos = self._template_manager.get_all_templates_sync()
        return [
            _dto_to_template(d) for d in dtos if getattr(d, "provider_api", None) == provider_api
        ]

    def find_active_templates(self) -> list[Template]:
        """Find all active templates."""
        self._logger.debug("Finding all active templates")
        dtos = self._template_manager.get_all_templates_sync()
        return [_dto_to_template(d) for d in dtos]

    def search_templates(self, criteria: dict[str, Any]) -> list[Template]:
        """Search templates by criteria."""
        self._logger.debug("Searching templates with criteria: %s", criteria)
        dtos = self._template_manager.get_all_templates_sync()
        filtered: list[Template] = []
        for dto in dtos:
            template = _dto_to_template(dto)
            matches = all(getattr(template, k, None) == v for k, v in criteria.items())
            if matches:
                filtered.append(template)
        return filtered

    # Convenience methods
    def get_by_id(self, template_id: str) -> Optional[Template]:
        """Get template by ID (convenience method, delegates to find_by_id)."""
        return self.find_by_id(template_id)

    def get_all(self) -> list[Template]:
        """Get all templates."""
        return self.find_active_templates()

    def exists(self, template_id: str) -> bool:
        """Check if template exists."""
        return self._template_manager.get_template(template_id) is not None

    def validate_template(self, template: Template) -> list[str]:
        """Validate template configuration."""
        dto = TemplateDTO.from_domain(template)
        validation_result = _run_async(self._template_manager.validate_template(dto))
        errors: list[str] = validation_result.get("errors", [])
        return errors if not validation_result.get("is_valid", True) else []


def create_template_repository_impl(
    template_manager: TemplateConfigurationManager, logger: LoggingPort
) -> TemplateRepositoryImpl:
    """Create template repository implementation."""
    return TemplateRepositoryImpl(template_manager, logger)
