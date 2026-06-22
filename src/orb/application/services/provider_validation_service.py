"""Service for validating provider availability and compatibility."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from orb.domain.base.exceptions import ApplicationError

if TYPE_CHECKING:
    from orb.domain.template.template_aggregate import Template

from orb.domain.base.ports import ContainerPort, LoggingPort, ProviderSelectionPort
from orb.domain.base.ports.provider_validation_port import ProviderValidationPort
from orb.domain.base.results import ProviderSelectionResult


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

    async def select_and_validate_provider(self, template: Template) -> ProviderSelectionResult:
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
