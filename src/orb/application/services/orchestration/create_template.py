"""Orchestrator for creating a template."""

from __future__ import annotations

from typing import Optional

from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import CreateTemplateInput, CreateTemplateOutput
from orb.application.template.commands import CreateTemplateCommand
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.template.ports.template_defaults_port import TemplateDefaultsPort


def _is_oci_template(configuration: dict, provider_name: Optional[str]) -> bool:
    provider_type = configuration.get("provider_type") or configuration.get("providerType")
    if isinstance(provider_type, str) and provider_type.lower() == "oci":
        return True
    if provider_name:
        prefix = provider_name.replace("_", "-", 1).split("-", maxsplit=1)[0]
        return prefix.lower() == "oci"
    return False


class CreateTemplateOrchestrator(OrchestratorBase[CreateTemplateInput, CreateTemplateOutput]):
    """Orchestrator for creating a new template."""

    def __init__(
        self,
        command_bus: CommandBusPort,
        query_bus: QueryBusPort,
        logger: LoggingPort,
        template_defaults_service: Optional[TemplateDefaultsPort] = None,
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus  # reserved for future query-side operations
        self._logger = logger
        self._template_defaults_service = template_defaults_service

    async def execute(self, input: CreateTemplateInput) -> CreateTemplateOutput:  # type: ignore[return]
        self._logger.info("CreateTemplateOrchestrator: template_id=%s", input.template_id)

        provider_name = (
            input.provider_name
            or input.configuration.get("provider_name")
            or input.configuration.get("providerName")
        )
        configuration = dict(input.configuration)
        if (
            _is_oci_template(configuration, provider_name)
            and self._template_defaults_service is not None
        ):
            try:
                configuration = self._template_defaults_service.resolve_template_defaults(
                    configuration, provider_instance_name=provider_name
                )
                self._logger.info(
                    "Resolved template defaults for template_id=%s provider=%s",
                    input.template_id,
                    provider_name,
                )
            except ValueError as exc:
                self._logger.warning(
                    "Could not resolve template defaults for template_id=%s: %s",
                    input.template_id,
                    exc,
                )

        provider_api = (
            input.provider_api
            or configuration.get("provider_api")
            or configuration.get("providerApi")
        )
        image_id = input.image_id or configuration.get("image_id") or configuration.get("imageId")
        instance_type = (
            input.instance_type
            or configuration.get("instance_type")
            or configuration.get("instanceType")
            or configuration.get("shape")
        )
        if not provider_api:
            raise ValueError("provider_api is required")
        if not image_id:
            raise ValueError("image_id is required")

        command = CreateTemplateCommand(
            template_id=input.template_id,
            provider_api=provider_api,
            image_id=image_id,
            name=input.name or configuration.get("name"),
            description=input.description or configuration.get("description"),
            instance_type=instance_type,
            tags=input.tags or configuration.get("tags", {}),
            configuration=configuration,
        )
        await self._command_bus.execute(command)

        return CreateTemplateOutput(
            template_id=input.template_id,
            created=command.created,
            validation_errors=command.validation_errors or [],
        )
