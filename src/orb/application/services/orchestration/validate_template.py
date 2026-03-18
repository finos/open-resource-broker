"""Orchestrator for validating a template."""

from __future__ import annotations

from orb.application.dto.queries import ValidateTemplateQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    ValidateTemplateInput,
    ValidateTemplateOutput,
)
from orb.domain.base.ports.logging_port import LoggingPort


class ValidateTemplateOrchestrator(OrchestratorBase[ValidateTemplateInput, ValidateTemplateOutput]):
    """Orchestrator for validating a template configuration."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: ValidateTemplateInput) -> ValidateTemplateOutput:  # type: ignore[return]
        self._logger.info(
            "ValidateTemplateOrchestrator: template_id=%s",
            input.template_id,
        )

        query = ValidateTemplateQuery(
            template_id=input.template_id,
            template_config=input.config or {},
        )
        result = await self._query_bus.execute(query)

        errors: list[str] = result.get("validation_errors", []) if isinstance(result, dict) else []
        valid: bool = result.get("valid", False) if isinstance(result, dict) else False
        message: str = result.get("message", "") if isinstance(result, dict) else ""

        return ValidateTemplateOutput(
            valid=valid,
            errors=errors,
            message=message,
            raw={
                "template_id": input.template_id,
                "status": "validated",
                "valid": valid,
                "validation_errors": errors,
                "message": message,
            },
        )
