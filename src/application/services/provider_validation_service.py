"""Service for validating provider availability and compatibility."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from domain.base.exceptions import ApplicationError

if TYPE_CHECKING:
    from domain.template.template_aggregate import Template

from domain.base.ports import ContainerPort, LoggingPort, ProviderSelectionPort
from domain.base.ports.configuration_port import ConfigurationPort
from domain.base.ports.provider_validation_port import ProviderValidationPort
from domain.base.results import ProviderSelectionResult


class ProviderValidationService:
    """Service for validating provider availability and template compatibility."""

    def __init__(
        self,
        container: ContainerPort,
        logger: LoggingPort,
        provider_selection_port: ProviderSelectionPort,
        validator: Optional[ProviderValidationPort] = None,
    ) -> None:
        self._container = container
        self.logger = logger
        self._provider_selection_port = provider_selection_port
        self._validator = validator

    async def validate_provider_availability(self) -> None:
        """Validate that providers are available."""
        config_manager = self._container.get(ConfigurationPort)
        provider_config = config_manager.get_provider_config()

        if provider_config:
            for provider_instance in provider_config.get_active_providers():
                self._provider_selection_port.register_provider_strategy(
                    provider_instance.type, provider_instance
                )

        available_strategies = self._provider_selection_port.get_available_strategies()
        if not available_strategies:
            error_msg = "No provider strategies available - cannot create machine requests"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        self.logger.debug("Available provider strategies: %s", available_strategies)

    async def select_and_validate_provider(self, template: "Template") -> ProviderSelectionResult:
        """Select provider and validate template compatibility."""
        selection_result = self._provider_selection_port.select_provider_for_template(template)
        self.logger.info(
            "Selected provider: %s (%s)",
            selection_result.provider_name,
            selection_result.selection_reason,
        )

        if self._validator is not None:
            template_dict = template if isinstance(template, dict) else vars(template)
            result = self._validator.validate_template_configuration(template_dict)
            if not result.get("valid", True):
                errors = result.get("errors", [])
                raise ApplicationError("; ".join(errors))

        validation_result = self._provider_selection_port.validate_template_requirements(
            template,
            selection_result.provider_name,
        )
        if validation_result.warnings:
            for warning in validation_result.warnings:
                self.logger.warning("Template validation warning: %s", warning)
        if not validation_result.is_valid:
            raise ApplicationError("; ".join(validation_result.errors))

        return selection_result
