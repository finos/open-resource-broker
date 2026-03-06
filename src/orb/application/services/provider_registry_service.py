"""Application service interface for provider registry access."""

from typing import Any

from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.results import ProviderSelectionResult, ValidationResult
from orb.domain.services.template_validation_domain_service import TemplateValidationDomainService
from orb.domain.template.template_aggregate import Template
from orb.providers.registry import ProviderRegistry


class ProviderRegistryService:
    """Application service interface for provider registry operations."""

    def __init__(
        self,
        registry: ProviderRegistry,
        validation_service: TemplateValidationDomainService,
        logger: LoggingPort,
    ):
        self._registry = registry
        self._validation_service = validation_service
        self._logger = logger

    def select_provider_for_template(
        self, template: Template, provider_name: str | None = None
    ) -> ProviderSelectionResult:
        return self._registry.select_provider_for_template(template, provider_name, self._logger)

    def select_active_provider(self) -> ProviderSelectionResult:
        return self._registry.select_active_provider(self._logger)

    def validate_template_requirements(
        self, template: Template, provider_instance: str
    ) -> ValidationResult:
        return self._validation_service.validate_template_requirements(template, provider_instance)

    async def execute_operation(self, provider_id: str, operation: Any) -> Any:
        strategy = self._registry.get_or_create_strategy(provider_id)
        if strategy is None:
            raise ValueError(f"No strategy found for provider: {provider_id}")
        return await strategy.execute_operation(operation)

    def get_strategy_capabilities(self, provider_id: str) -> Any:
        strategy = self._registry.get_or_create_strategy(provider_id)
        if strategy is None:
            return None
        return strategy.get_capabilities()

    def get_available_strategies(self) -> list[str]:
        return (
            self._registry.get_registered_providers()
            + self._registry.get_registered_provider_instances()
        )

    def register_provider_strategy(self, provider_type: str, config: Any = None) -> bool:
        return self._registry.ensure_provider_type_registered(provider_type)

    def check_strategy_health(self, provider_id: str) -> Any:
        strategy = self._registry.get_or_create_strategy(provider_id)
        if strategy is None:
            return None
        return strategy.check_health()

    def update_provider_health(self, provider_name: str, health_data: dict) -> None:
        """Persist health state for a provider into the registry."""
        self._registry.update_provider_health(provider_name, health_data)
