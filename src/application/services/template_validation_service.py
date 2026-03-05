"""Generic template validation service."""

from domain.base.ports.logging_port import LoggingPort
from domain.base.results import ValidationResult
from domain.services.template_validation_domain_service import TemplateValidationDomainService
from domain.template.template_aggregate import Template


class TemplateValidationService:
    """Generic template validation service."""

    def __init__(self, validation_service: TemplateValidationDomainService, logger: LoggingPort):
        self._validation_service = validation_service
        self._logger = logger

    async def validate_template_requirements(
        self, template: Template, provider_instance: str
    ) -> ValidationResult:
        """Validate template against provider capabilities."""
        return self._validation_service.validate_template_requirements(template, provider_instance)
