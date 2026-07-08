"""Application service interface for provider registry access."""

from typing import Any

from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort
from orb.domain.base.results import ProviderSelectionResult, ValidationResult
from orb.domain.services.template_validation_domain_service import TemplateValidationDomainService
from orb.domain.template.template_aggregate import Template


class ProviderRegistryService:
    """Application service interface for provider registry operations."""

    def __init__(
        self,
        registry: ProviderRegistryPort,
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

    def select_active_provider(
        self,
        *,
        provider_name: str | None = None,
        provider_type: str | None = None,
    ) -> ProviderSelectionResult:
        return self._registry.select_active_provider(
            self._logger, provider_name=provider_name, provider_type=provider_type
        )

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

    def resolve_api_alias(self, provider_id: str, raw_api: str) -> str:
        """Resolve a provider API name to its canonical form via the named strategy.

        Returns raw_api unchanged if the strategy is not found.
        """
        strategy = self._registry.get_or_create_strategy(provider_id)
        if strategy is None:
            return raw_api
        return strategy.resolve_api_alias(raw_api)

    def get_registered_provider_types(self) -> list[str]:
        """Return registered provider type names."""
        return self._registry.get_registered_providers()

    def ensure_provider_registered(self, provider_type: str) -> bool:
        """Ensure a provider type is registered. Returns True on success."""
        return self._registry.ensure_provider_type_registered(provider_type)

    def get_or_create_strategy(self, provider_identifier: str, config: Any = None) -> Any:
        """Get or create a provider strategy instance."""
        return self._registry.get_or_create_strategy(provider_identifier, config)
