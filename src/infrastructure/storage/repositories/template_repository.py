"""Template repository implementation using storage strategy composition."""

from typing import Any, Optional

from domain.template.repository import TemplateRepository as TemplateRepositoryInterface
from domain.template.template_aggregate import Template
from domain.template.value_objects import TemplateId
from infrastructure.error.decorators import handle_infrastructure_exceptions
from infrastructure.logging.logger import get_logger
from infrastructure.storage.base.strategy import BaseStorageStrategy


class TemplateSerializer:
    """Handles Template aggregate serialization/deserialization."""

    def to_dict(self, template: Template) -> dict[str, Any]:
        """Convert Template to storage format using domain serialization."""
        data = template.model_dump()
        
        # Process value objects (unwrap .value attributes)
        from infrastructure.utilities.common.serialization import process_value_objects
        data = process_value_objects(data)
        
        # Add storage-specific metadata
        data["schema_version"] = "2.0.0"
        
        return data
    
    def from_dict(self, data: dict[str, Any]) -> Template:
        """Convert storage format to Template using domain validation."""
        return Template.model_validate(data)


class TemplateRepositoryImpl(TemplateRepositoryInterface):
    """Template repository implementation using storage strategy composition."""

    def __init__(self, storage_strategy: BaseStorageStrategy) -> None:
        """Initialize repository with storage strategy."""
        self.storage_strategy = storage_strategy
        self.serializer = TemplateSerializer()
        self.logger = get_logger(__name__)

    @handle_infrastructure_exceptions(context="template_save")
    def save(self, template: Template) -> list[Any]:
        """Save template using storage strategy and return extracted events."""
        try:
            # Save the template
            template_data = self.serializer.to_dict(template)
            self.storage_strategy.save(str(template.template_id.value), template_data)

            # Extract events from the aggregate
            events = template.get_domain_events()
            template.clear_domain_events()

            self.logger.debug(
                "Saved template %s and extracted %s events",
                template.template_id,
                len(events),
            )
            return events

        except Exception as e:
            self.logger.error("Failed to save template %s: %s", template.template_id, e)
            raise

    @handle_infrastructure_exceptions(context="template_retrieval")
    def get_by_id(self, template_id: TemplateId) -> Optional[Template]:
        """Get template by ID using storage strategy."""
        try:
            data = self.storage_strategy.find_by_id(str(template_id.value))
            if data:
                return self.serializer.from_dict(data)
            return None
        except Exception as e:
            self.logger.error("Failed to get template %s: %s", template_id, e)
            raise

    @handle_infrastructure_exceptions(context="template_retrieval")
    def find_by_id(self, template_id: TemplateId) -> Optional[Template]:
        """Find template by ID (alias for get_by_id)."""
        return self.get_by_id(template_id)

    @handle_infrastructure_exceptions(context="template_search")
    def find_by_template_id(self, template_id: str) -> Optional[Template]:
        """Find template by template ID string."""
        try:
            return self.get_by_id(TemplateId(value=template_id))
        except Exception as e:
            self.logger.error("Failed to find template by template_id %s: %s", template_id, e)
            raise

    @handle_infrastructure_exceptions(context="template_search")
    def find_by_name(self, name: str) -> Optional[Template]:
        """Find template by name."""
        try:
            criteria = {"name": name}
            data_list = self.storage_strategy.find_by_criteria(criteria)
            if data_list:
                return self.serializer.from_dict(data_list[0])
            return None
        except Exception as e:
            self.logger.error("Failed to find template by name %s: %s", name, e)
            raise

    @handle_infrastructure_exceptions(context="template_search")
    def find_active_templates(self) -> list[Template]:
        """Find active templates."""
        try:
            criteria = {"is_active": True}
            data_list = self.storage_strategy.find_by_criteria(criteria)
            return [self.serializer.from_dict(data) for data in data_list]
        except Exception as e:
            self.logger.error("Failed to find active templates: %s", e)
            raise

    @handle_infrastructure_exceptions(context="template_search")
    def find_by_provider_api(self, provider_api: str) -> list[Template]:
        """Find templates by provider API."""
        try:
            criteria = {"provider_api": provider_api}
            data_list = self.storage_strategy.find_by_criteria(criteria)
            return [self.serializer.from_dict(data) for data in data_list]
        except Exception as e:
            self.logger.error("Failed to find templates by provider_api %s: %s", provider_api, e)
            raise

    @handle_infrastructure_exceptions(context="template_search")
    def find_all(self) -> list[Template]:
        """Find all templates."""
        try:
            all_data = self.storage_strategy.find_all()
            return [self.serializer.from_dict(data) for data in all_data.values()]
        except Exception as e:
            self.logger.error("Failed to find all templates: %s", e)
            raise

    def get_all(self) -> list[Template]:
        """Get all templates - alias for find_all for backward compatibility."""
        return self.find_all()

    @handle_infrastructure_exceptions(context="template_search")
    def search_templates(self, criteria: dict[str, Any]) -> list[Template]:
        """Search templates by criteria."""
        try:
            data_list = self.storage_strategy.find_by_criteria(criteria)
            return [self.serializer.from_dict(data) for data in data_list]
        except Exception as e:
            self.logger.error("Failed to search templates with criteria %s: %s", criteria, e)
            raise

    @handle_infrastructure_exceptions(context="template_deletion")
    def delete(self, template_id: TemplateId) -> None:
        """Delete template by ID."""
        try:
            self.storage_strategy.delete(str(template_id.value))
            self.logger.debug("Deleted template %s", template_id)
        except Exception as e:
            self.logger.error("Failed to delete template %s: %s", template_id, e)
            raise

    @handle_infrastructure_exceptions(context="template_existence_check")
    def exists(self, template_id: TemplateId) -> bool:
        """Check if template exists."""
        try:
            return self.storage_strategy.exists(str(template_id.value))
        except Exception as e:
            self.logger.error("Failed to check if template %s exists: %s", template_id, e)
            raise
