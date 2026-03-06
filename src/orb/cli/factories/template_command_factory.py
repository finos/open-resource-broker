"""Template command factory for creating template-related commands and queries."""

from typing import Any, Optional

from orb.application.dto.bulk_queries import GetMultipleTemplatesQuery
from orb.application.dto.queries import GetTemplateQuery, ListTemplatesQuery, ValidateTemplateQuery
from orb.application.template.commands import (
    CreateTemplateCommand,
    DeleteTemplateCommand,
    UpdateTemplateCommand,
)


class TemplateCommandFactory:
    """Factory for creating template-related commands and queries."""

    def create_list_templates_query(
        self,
        provider_name: Optional[str] = None,
        active_only: bool = True,
        filter_expressions: Optional[list] = None,
        limit: Optional[int] = 50,
        offset: Optional[int] = 0,
        **kwargs: Any,
    ) -> ListTemplatesQuery:
        """Create query to list templates."""
        return ListTemplatesQuery(
            provider_name=provider_name,
            active_only=active_only,
            filter_expressions=filter_expressions or [],
            limit=min(limit or 50, 1000),  # type: ignore[arg-type]
            offset=offset or 0,  # type: ignore[arg-type]
        )

    def create_get_template_query(
        self,
        template_id: str,
        provider: Optional[str] = None,
        **kwargs: Any,
    ) -> GetTemplateQuery:
        """Create query to get template by ID."""
        provider_name = kwargs.get("provider_name") or provider
        return GetTemplateQuery(template_id=template_id, provider_name=provider_name)

    def create_create_template_command(
        self,
        template_id: str,
        provider_name: str,
        handler_type: str,
        configuration: dict,
        description: Optional[str] = None,
        tags: Optional[dict] = None,
        **kwargs: Any,
    ) -> CreateTemplateCommand:
        """Create command to create template."""
        return CreateTemplateCommand(
            template_id=template_id,
            provider_api=provider_name,
            image_id=handler_type,
            configuration=configuration,
            description=description,
        )

    def create_update_template_command(
        self,
        template_id: str,
        configuration: Optional[dict] = None,
        description: Optional[str] = None,
        tags: Optional[dict] = None,
        **kwargs: Any,
    ) -> UpdateTemplateCommand:
        """Create command to update template."""
        return UpdateTemplateCommand(
            template_id=template_id,
            configuration=configuration or {},
            description=description,
        )

    def create_delete_template_command(
        self, template_id: str, **kwargs: Any
    ) -> DeleteTemplateCommand:
        """Create command to delete template."""
        return DeleteTemplateCommand(template_id=template_id)

    def create_validate_template_query(
        self,
        template_config: dict,
        template_id: Optional[str] = None,
        **kwargs: Any,
    ) -> ValidateTemplateQuery:
        """Create query to validate template configuration."""
        return ValidateTemplateQuery(template_config=template_config, template_id=template_id)

    def create_get_multiple_templates_query(
        self,
        template_ids: list[str],
        provider_name: Optional[str] = None,
        active_only: bool = True,
        **kwargs: Any,
    ) -> GetMultipleTemplatesQuery:
        """Create query to get multiple templates by IDs."""
        return GetMultipleTemplatesQuery(
            template_ids=template_ids, provider_name=provider_name, active_only=active_only
        )
