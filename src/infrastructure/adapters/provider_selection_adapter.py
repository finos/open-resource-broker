"""Infrastructure adapter for provider selection operations."""

from typing import Any

from application.services.provider_registry_service import ProviderRegistryService
from domain.base.ports.provider_selection_port import ProviderSelectionPort
from domain.base.results import ProviderSelectionResult, ValidationResult
from domain.template.template_aggregate import Template


class ProviderSelectionAdapter(ProviderSelectionPort):
    """Infrastructure adapter implementing ProviderSelectionPort.

    This adapter wraps ProviderRegistryService to provide a clean domain
    interface while delegating to infrastructure-layer services.
    """

    def __init__(self, provider_registry_service: ProviderRegistryService):
        """Initialize adapter with provider registry service.

        Args:
            provider_registry_service: Application service for provider operations
        """
        self._service = provider_registry_service

    def select_provider_for_template(
        self, template: Template, provider_name: str | None = None
    ) -> ProviderSelectionResult:
        """Select provider instance for template requirements."""
        return self._service.select_provider_for_template(template, provider_name)

    def select_active_provider(self) -> ProviderSelectionResult:
        """Select active provider instance from configuration."""
        return self._service.select_active_provider()

    def validate_template_requirements(
        self, template: Template, provider_instance: str
    ) -> ValidationResult:
        """Validate template requirements against provider capabilities."""
        return self._service.validate_template_requirements(template, provider_instance)

    async def execute_operation(self, provider_id: str, operation: Any) -> Any:
        """Execute operation using provider strategy."""
        return await self._service.execute_operation(provider_id, operation)

    def get_strategy_capabilities(self, provider_id: str) -> Any:
        """Get capabilities of provider strategy."""
        return self._service.get_strategy_capabilities(provider_id)

    def get_available_strategies(self) -> list[str]:
        """Get list of available provider strategies."""
        return self._service.get_available_strategies()

    def register_provider_strategy(self, provider_type: str, config: Any = None) -> bool:
        """Register a provider strategy."""
        return self._service.register_provider_strategy(provider_type, config)

    def check_strategy_health(self, provider_id: str) -> Any:
        """Check health of provider strategy."""
        return self._service.check_strategy_health(provider_id)
