"""Application service interface for provider registry access."""

from typing import Any, Optional

from domain.base.ports.logging_port import LoggingPort
from domain.services.provider_selection_service import ProviderSelectionService
from domain.template.template_aggregate import Template
from providers.registry import ProviderRegistry
from providers.results import ProviderSelectionResult, ValidationResult


class ProviderRegistryService:
    """Application service interface for provider registry operations."""
    
    def __init__(
        self, 
        registry: ProviderRegistry,
        selection_service: ProviderSelectionService,
        logger: LoggingPort
    ):
        self._registry = registry
        self._selection_service = selection_service
        self._logger = logger
    
    def select_provider_for_template(self, template: Template) -> ProviderSelectionResult:
        """Select provider instance for template requirements."""
        return self._selection_service.select_provider_for_template(template)
    
    def select_active_provider(self) -> ProviderSelectionResult:
        """Select active provider instance from configuration."""
        return self._selection_service.select_active_provider()
    
    def validate_template_requirements(self, template: Template, provider_instance: str) -> ValidationResult:
        """Validate template requirements against provider capabilities."""
        from providers.registry import get_provider_registry
        registry = get_provider_registry()
        return registry.validate_template_requirements(template, provider_instance)
    
    async def execute_operation(self, provider_id: str, operation: Any) -> Any:
        """Execute operation using provider strategy."""
        strategy = self._registry.get_strategy(provider_id)
        return await strategy.execute_operation(operation)
    
    def get_strategy_capabilities(self, provider_id: str) -> Any:
        """Get capabilities of provider strategy."""
        strategy = self._registry.get_strategy(provider_id)
        return strategy.get_capabilities()
    
    def check_strategy_health(self, provider_id: str) -> Any:
        """Check health of provider strategy."""
        strategy = self._registry.get_strategy(provider_id)
        return strategy.check_health()