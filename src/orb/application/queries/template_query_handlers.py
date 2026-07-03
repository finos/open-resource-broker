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
from orb.application.services.orchestration.dtos import Paginated
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports import ContainerPort, ErrorHandlingPort, LoggingPort
from orb.domain.services.generic_filter_service import GenericFilterService
from orb.domain.template.factory import TemplateFactoryPort
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
        template_factory: TemplateFactoryPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self._container = container
        self._template_factory = template_factory

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

            resolved_template = self._template_factory.create_template(resolved_data)

            self.logger.info("Retrieved template: %s", query.template_id)
            return TemplateDTO.from_domain(resolved_template)  # type: ignore[return-value]

        except EntityNotFoundError:
            self.logger.error("Template not found: %s", query.template_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get template: %s", e)
            raise


@query_handler(ListTemplatesQuery)
class ListTemplatesHandler(BaseQueryHandler[ListTemplatesQuery, Paginated[TemplateDTOPort]]):
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

    async def execute_query(self, query: ListTemplatesQuery) -> Paginated[TemplateDTOPort]:
        """Execute list templates query.

        Pipeline: load → filters → active_only → q → sort → total → slice.
        Filter/sort/q all run on the FULL dataset so the slice is honest:
        if there are 200 q-matches in 10k rows, page 2 still shows them.
        """
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

            total_unfiltered = len(template_dtos)

            if query.provider_type:
                template_dtos = [
                    t
                    for t in template_dtos
                    if getattr(t, "provider_type", None) == query.provider_type
                ]

            # active_only filter runs first, while items are still DTOs and the
            # is_active attribute is reliably present.  filter_expressions may
            # convert items to plain dicts (via model_dump), after which
            # getattr(t, "is_active", True) would always return True.
            if query.active_only:
                template_dtos = [t for t in template_dtos if getattr(t, "is_active", True)]

            if query.filter_expressions:
                template_dicts = [dto.model_dump() for dto in template_dtos]
                filtered_dicts = self._generic_filter_service.apply_filters(
                    template_dicts, query.filter_expressions
                )
                template_dtos = filtered_dicts  # type: ignore[assignment]

            # q: case-insensitive substring search across user-visible fields
            if query.q:
                needle = query.q.lower()
                searchable = ("template_id", "name", "description", "image_id")
                template_dtos = [
                    t
                    for t in template_dtos
                    if any(
                        needle
                        in str(t.get(f, "") if isinstance(t, dict) else getattr(t, f, "")).lower()
                        for f in searchable
                    )
                ]

            # sort: "+field" / "-field"; missing prefix == asc
            if query.sort:
                sort_key = query.sort
                descending = sort_key.startswith("-")
                attr = sort_key.lstrip("-+")

                def _val(t: Any) -> str:
                    raw = t.get(attr, "") if isinstance(t, dict) else getattr(t, attr, "")
                    return "" if raw is None else str(raw)

                try:
                    template_dtos = sorted(template_dtos, key=_val, reverse=descending)
                except TypeError as exc:
                    self.logger.warning(
                        "ListTemplates sort failed on attr=%s descending=%s: %s",
                        attr,
                        descending,
                        exc,
                    )

            # total AFTER filter+sort, BEFORE slice — this is what the
            # client expects as the pagination denominator.
            total_count = len(template_dtos)

            # Slice. None limit → no cap.
            offset = query.offset or 0
            if query.limit is None:
                page = template_dtos[offset:]
            else:
                limit = min(query.limit, 1000)
                if query.limit > 1000:
                    self.logger.warning(
                        "ListTemplatesQuery.limit=%d clamped to 1000; "
                        "total_count=%d. Consumers needing full counts "
                        "should rely on total_count, not len(templates).",
                        query.limit,
                        total_count,
                    )
                page = template_dtos[offset : offset + limit] if limit > 0 else []

            self.logger.info(
                "Found %s templates (total: %s, unfiltered: %s, limit: %s, offset: %s)",
                len(page),
                total_count,
                total_unfiltered,
                query.limit,
                offset,
            )
            return Paginated(
                items=list(page),  # type: ignore[arg-type]
                total_count=total_count,
                total_unfiltered=total_unfiltered,
            )

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

                template_config = template_dto.model_dump(exclude_none=True)
                template_config["template_id"] = template_dto.template_id

            except EntityNotFoundError:
                self.logger.error("Template not found: %s", template_id)
                raise
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
