"""Domain port for provider selection operations."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from domain.base.results import ProviderSelectionResult, ValidationResult

if TYPE_CHECKING:
    from domain.template.template_aggregate import Template


class ProviderSelectionPort(ABC):
    """Domain port for provider selection and operation execution.

    This port abstracts provider registry operations to maintain clean
    architecture boundaries between application and infrastructure layers.
    """

    @abstractmethod
    def select_provider_for_template(
        self, template: "Template", provider_name: Optional[str] = None
    ) -> ProviderSelectionResult:
        """Select provider instance for template requirements.

        Args:
            template: Template aggregate with resource requirements
            provider_name: Optional specific provider to use

        Returns:
            ProviderSelectionResult with selected provider details
        """

    @abstractmethod
    def select_active_provider(self) -> ProviderSelectionResult:
        """Select active provider instance from configuration.

        Returns:
            ProviderSelectionResult with active provider details
        """

    @abstractmethod
    def validate_template_requirements(
        self, template: "Template", provider_instance: str
    ) -> ValidationResult:
        """Validate template requirements against provider capabilities.

        Args:
            template: Template aggregate to validate
            provider_instance: Provider instance name

        Returns:
            ValidationResult with validation status and details
        """

    @abstractmethod
    async def execute_operation(self, provider_id: str, operation: Any) -> Any:
        """Execute operation using provider strategy.

        Args:
            provider_id: Provider instance identifier
            operation: Provider operation to execute

        Returns:
            Operation result from provider strategy
        """

    @abstractmethod
    def get_strategy_capabilities(self, provider_id: str) -> Any:
        """Get capabilities of provider strategy.

        Args:
            provider_id: Provider instance identifier

        Returns:
            Provider strategy capabilities or None if not found
        """

    @abstractmethod
    def get_available_strategies(self) -> list[str]:
        """Get list of available provider strategies.

        Returns:
            List of registered provider strategy identifiers
        """

    @abstractmethod
    def register_provider_strategy(self, provider_type: str, config: Any = None) -> bool:
        """Register a provider strategy.

        Args:
            provider_type: Type of provider to register
            config: Optional provider configuration

        Returns:
            True if registration successful, False otherwise
        """

    @abstractmethod
    def check_strategy_health(self, provider_id: str) -> Any:
        """Check health of provider strategy.

        Args:
            provider_id: Provider instance identifier

        Returns:
            Health check result or None if strategy not found
        """
