"""Query handlers for template domain queries."""

from __future__ import annotations

from typing import Any

from orb.application.base.handlers import BaseQueryHandler
from orb.application.decorators import query_handler
from orb.application.dto.queries import (
    GetConfigurationQuery,
    GetTemplateQuery,
    ListTemplatesQuery,
    ValidateTemplateQuery,
)
from orb.application.dto.system import ValidationDTO
from orb.application.ports.template_dto_port import TemplateDTOPort
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports import ContainerPort, ErrorHandlingPort, LoggingPort
from orb.domain.services.generic_filter_service import GenericFilterService
from orb.domain.template.factory import TemplateFactory, get_default_template_factory
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.template.dtos import TemplateDTO


@query_handler(GetTemplateQuery)
class GetTemplateHandler(BaseQueryHandler[GetTemplateQuery, TemplateDTOPort]):
    """Handler for getting template details."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self._container = container

    async def execute_query(self, query: GetTemplateQuery) -> Template:  # type: ignore[override]
        """Execute get template query."""
        from orb.domain.base.ports import TemplateConfigurationPort

        self.logger.info("Getting template: %s", query.template_id)

        try:
            template_manager = self._container.get(TemplateConfigurationPort)
            template_dto = await template_manager.get_template_by_id(query.template_id)

            if not template_dto:
                raise EntityNotFoundError("Template", query.template_id)

            template_data = template_dto.model_dump()
            template_data.setdefault("template_id", template_dto.template_id)
            template_data.setdefault("name", template_dto.name or template_dto.template_id)
            template_data.setdefault("provider_api", template_dto.provider_api)

            from orb.application.services.template_defaults_service import TemplateDefaultsService

            if self._container.has(TemplateDefaultsService):
                template_defaults_service = self._container.get(TemplateDefaultsService)
                resolved_data = template_defaults_service.resolve_template_defaults(
                    template_data,
                    provider_name=query.provider_name,  # type: ignore[call-arg]
                )
            else:
                resolved_data = template_data

            if self._container.has(TemplateFactory):
                template_factory = self._container.get(TemplateFactory)
            else:
                template_factory = get_default_template_factory()

            resolved_template = template_factory.create_template(resolved_data)

            self.logger.info("Retrieved template: %s", query.template_id)
            return TemplateDTO.from_domain(resolved_template)  # type: ignore[return-value]

        except EntityNotFoundError:
            self.logger.error("Template not found: %s", query.template_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get template: %s", e)
            raise


@query_handler(ListTemplatesQuery)
class ListTemplatesHandler(BaseQueryHandler[ListTemplatesQuery, list[TemplateDTOPort]]):
    """Handler for listing templates."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
        generic_filter_service: GenericFilterService,
    ) -> None:
        super().__init__(logger, error_handler)
        self._container = container
        self._generic_filter_service = generic_filter_service

    async def execute_query(self, query: ListTemplatesQuery) -> list[TemplateDTOPort]:
        """Execute list templates query - returns raw templates for scheduler formatting."""
        from orb.domain.base.ports import TemplateConfigurationPort

        self.logger.info("Listing templates")

        try:
            template_manager = self._container.get(TemplateConfigurationPort)

            if query.provider_name:
                template_dtos = await template_manager.load_templates(
                    provider_override=query.provider_name
                )
            elif query.provider_api:
                template_dtos = await template_manager.get_templates_by_provider(query.provider_api)
            else:
                template_dtos = await template_manager.load_templates()

            if query.filter_expressions:
                template_dicts = [dto.model_dump() for dto in template_dtos]
                filtered_dicts = self._generic_filter_service.apply_filters(
                    template_dicts, query.filter_expressions
                )
                template_dtos = filtered_dicts  # type: ignore[assignment]

            total_count = len(template_dtos)
            limit = min(query.limit or 50, 1000)  # type: ignore[union-attr]
            offset = query.offset or 0  # type: ignore[union-attr]

            self.logger.info(
                "Found %s templates (total: %s, limit: %s, offset: %s)",
                len(template_dtos),
                total_count,
                limit,
                offset,
            )
            return template_dtos  # type: ignore[return-value]

        except Exception as e:
            self.logger.error("Failed to list templates: %s", e)
            raise


@query_handler(ValidateTemplateQuery)
class ValidateTemplateHandler(BaseQueryHandler[ValidateTemplateQuery, ValidationDTO]):
    """Handler for validating template configuration."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: ValidateTemplateQuery) -> dict[str, Any]:  # type: ignore[override]
        """Execute validate template query."""
        template_config = query.template_config
        template_id = getattr(query, "template_id", None)

        if not template_id and isinstance(template_config, dict):
            template_id = template_config.get("template_id")

        if template_id and (not template_config or template_config == {"template_id": template_id}):
            self.logger.info("Loading template from storage: %s", template_id)
            try:
                from orb.domain.base.ports import TemplateConfigurationPort

                template_manager = self.container.get(TemplateConfigurationPort)
                template_dto = await template_manager.get_template_by_id(template_id)

                if not template_dto:
                    from orb.domain.base.exceptions import EntityNotFoundError

                    raise EntityNotFoundError("Template", template_id)

                template_config = template_dto.configuration or {}
                template_config["template_id"] = template_dto.template_id

            except Exception as e:
                self.logger.error("Failed to load template %s: %s", template_id, e)
                return {
                    "template_id": template_id,
                    "success": False,
                    "valid": False,
                    "message": f"Failed to load template: {e}",
                    "error": f"Failed to load template: {e}",
                }

        self.logger.info("Validating template: %s", template_id or "file-template")

        try:
            from orb.domain.base.ports.template_configuration_port import TemplateConfigurationPort

            template_port = self.container.get(TemplateConfigurationPort)
            validation_errors = template_port.validate_template_config(template_config)

            if validation_errors:
                self.logger.warning(
                    "Template validation failed for %s: %s",
                    template_id or "file-template",
                    validation_errors,
                )
            else:
                self.logger.info(
                    "Template validation passed for %s", template_id or "file-template"
                )

            success = len(validation_errors) == 0
            message = (
                "Template validation passed"
                if success
                else f"Template validation failed: {', '.join(validation_errors)}"
                if validation_errors
                else "Template validation failed"
            )

            return {
                "template_id": template_id or template_config.get("template_id", "file-template"),
                "success": success,
                "valid": success,
                "message": message,
                "validation_errors": validation_errors,
                "configuration": template_config,
            }

        except Exception as e:
            self.logger.error(
                "Template validation failed for %s: %s", template_id or "file-template", e
            )
            validation_errors = [f"Validation error: {e!s}"]
            return {
                "template_id": template_id or template_config.get("template_id", "file-template"),
                "success": False,
                "valid": False,
                "message": f"Template validation failed: {validation_errors[0]}",
                "validation_errors": validation_errors,
                "configuration": template_config,
            }


@query_handler(GetConfigurationQuery)
class GetConfigurationHandler(BaseQueryHandler[GetConfigurationQuery, dict[str, Any]]):
    """Handler for getting configuration values."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize get configuration handler."""
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: GetConfigurationQuery) -> dict[str, Any]:
        """Execute get configuration query."""
        self.logger.info("Getting configuration value for key: %s", query.key)

        try:
            from orb.domain.base.ports import ConfigurationPort

            config_manager = self.container.get(ConfigurationPort)
            value = config_manager.get_configuration_value(query.key, query.default)

            return {
                "key": query.key,
                "value": value,
                "default": query.default,
            }

        except Exception as e:
            self.logger.error("Failed to get configuration: %s", e)
            raise
