"""Orchestrator for validating a template."""

from __future__ import annotations

from typing import Optional

from orb.application.dto.queries import ValidateTemplateQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    ValidateTemplateInput,
    ValidateTemplateOutput,
)
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.template.ports.template_defaults_port import TemplateDefaultsPort


class ValidateTemplateOrchestrator(OrchestratorBase[ValidateTemplateInput, ValidateTemplateOutput]):
    """Orchestrator for validating a template configuration."""

    def __init__(
        self,
        command_bus: CommandBusPort,
        query_bus: QueryBusPort,
        logger: LoggingPort,
        template_defaults_service: Optional[TemplateDefaultsPort] = None,
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger
        self._template_defaults_service = template_defaults_service

    async def execute(self, input: ValidateTemplateInput) -> ValidateTemplateOutput:  # type: ignore[return]
        self._logger.info(
            "ValidateTemplateOrchestrator: template_id=%s",
            input.template_id,
        )

        template_config = input.config or {}
        if template_config and self._template_defaults_service is not None:
            try:
                template_config = self._template_defaults_service.resolve_template_defaults(
                    template_config,
                    provider_instance_name=input.provider_name,
                )
            except ValueError as exc:
                self._logger.warning(
                    "Could not resolve template defaults for template_id=%s: %s",
                    input.template_id,
                    exc,
                )

        query = ValidateTemplateQuery(
            template_id=input.template_id,
            template_config=template_config,
        )
        result = await self._query_bus.execute(query)

        errors: list[str] = result.get("validation_errors", []) if isinstance(result, dict) else []
        valid: bool = result.get("valid", False) if isinstance(result, dict) else False
        message: str = result.get("message", "") if isinstance(result, dict) else ""

        return ValidateTemplateOutput(
            valid=valid,
            errors=errors,
            message=message,
            template_id=input.template_id,
        )
