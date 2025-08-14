"""Provider Selection Service - Multi-provider routing and load balancing.

This service implements the business logic for selecting appropriate providers
based on template requirements, following DDD and Clean Architecture principles.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from src.config.managers.configuration_manager import ConfigurationManager
from src.config.schemas.provider_strategy_schema import ProviderInstanceConfig
from src.domain.base.dependency_injection import injectable
from src.domain.base.ports import LoggingPort
from src.domain.template.aggregate import Template
from src.infrastructure.registry.provider_registry import ProviderRegistry


@dataclass
class ProviderSelectionResult:
    """Result of provider selection process."""

    provider_type: str
    provider_instance: str
    selection_reason: str
    confidence: float = 1.0
    alternatives: List[str] = None

    def __post_init__(self):
        if self.alternatives is None:
            self.alternatives = []


class SelectionStrategy(str, Enum):
    """Provider selection strategies."""

    EXPLICIT = "explicit"  # Template specifies exact provider
    LOAD_BALANCED = "load_balanced"  # Load balance across provider type
    CAPABILITY_BASED = "capability_based"  # Select based on API capabilities
    DEFAULT = "default"  # Use configuration default


@injectable
class ProviderSelectionService:
    """
    Service for selecting appropriate provider based on template requirements.

    This service implements the core business logic for multi-provider routing,
    following the Single Responsibility Principle and Domain-Driven Design.

    Responsibilities:
    - Analyze template provider requirements
    - Apply selection strategies (explicit, load-balanced, capability-based)
    - Validate provider availability and health
    - Return selection results with reasoning
    """

    def __init__(
        self,
        config_manager: ConfigurationManager,
        logger: LoggingPort,
        provider_registry: Optional[ProviderRegistry] = None,
    ):
        """
        Initialize provider selection service.

        Args:
            config_manager: Configuration manager for provider settings
            logger: Logger for selection decisions and debugging
            provider_registry: Optional provider registry for capability checks
        """
        self._config_manager = config_manager
        self._logger = logger
        self._provider_registry = provider_registry
        self._provider_config = config_manager.get_provider_config()

        # Cache for provider selection results
        self._active_provider_cache: Optional[ProviderSelectionResult] = None

        if not self._provider_config:
            self._logger.warning(
                "No provider configuration found - provider selection may be limited"
            )

    def select_provider_for_template(self, template: Template) -> ProviderSelectionResult:
        """
        Select provider type and instance for template.

        This method implements the core selection algorithm following these priorities:
        1. Explicit provider instance selection (template.provider_name)
        2. Provider type with load balancing (template.provider_type)
        3. Auto-selection based on API capabilities (template.provider_api)
        4. Fallback to configuration default

        Args:
            template: Template requiring provider selection

        Returns:
            ProviderSelectionResult with selected provider and reasoning

        Raises:
            ValueError: If no suitable provider can be found
        """
        self._logger.info(f"Selecting provider for template: {template.template_id}")

        # Strategy 1: Explicit provider instance selection
        if template.provider_name:
            return self._select_explicit_provider(template)

        # Strategy 2: Provider type with load balancing
        if template.provider_type:
            return self._select_load_balanced_provider(template)

        # Strategy 3: Auto-selection based on API capabilities
        if template.provider_api:
            return self._select_by_api_capability(template)

        # Strategy 4: Fallback to default
        return self._select_default_provider(template)

    def select_active_provider(self) -> ProviderSelectionResult:
        """
        Select active provider based on selection policy (non-template specific).

        This method implements general provider selection for scenarios where
        no template context is available (e.g., configuration loading, file paths).
        Results are cached to avoid multiple selections.

        Returns:
            ProviderSelectionResult with selected provider and reasoning

        Raises:
            ValueError: If no suitable provider can be found
        """
        # Return cached result if available
        if self._active_provider_cache is not None:
            return self._active_provider_cache

        self._logger.debug("Selecting active provider using selection policy")

        # Get active providers based on selection policy
        active_providers = self._provider_config.get_active_providers()
        if not active_providers:
            raise ValueError("No active providers found in configuration")

        # Apply selection policy for multi-provider scenarios
        if len(active_providers) == 1:
            selected = active_providers[0]
            reason = "single_active_provider"
        else:
            # Use load balancing strategy for multiple providers
            selected = self._apply_load_balancing_strategy(
                active_providers, self._provider_config.selection_policy
            )
            reason = f"load_balanced_{self._provider_config.selection_policy.lower()}"

        result = ProviderSelectionResult(
            provider_type=selected.type,
            provider_instance=selected.name,
            selection_reason=reason,
            confidence=1.0,
            alternatives=[p.name for p in active_providers if p.name != selected.name]
        )

        # Cache the result
        self._active_provider_cache = result

        self._logger.info(f"Selected active provider: {selected.name} ({reason})")

        return result

    def _select_explicit_provider(self, template: Template) -> ProviderSelectionResult:
        """Select explicitly specified provider instance."""
        provider_name = template.provider_name

        # Validate provider instance exists and is enabled
        provider_instance = self._get_provider_instance_config(provider_name)
        if not provider_instance:
            raise ValueError(
                f"Provider instance '{provider_name}' not found in configuration")

        if not provider_instance.enabled:
            raise ValueError(f"Provider instance '{provider_name}' is disabled")

        self._logger.info(f"Selected explicit provider: {provider_name}")

        return ProviderSelectionResult(
            provider_type=provider_instance.type,
            provider_instance=provider_name,
            selection_reason="Explicitly specified in template",
            confidence=1.0,
        )

    def _select_load_balanced_provider(self, template: Template) -> ProviderSelectionResult:
        """Select provider instance using load balancing within provider type."""
        provider_type = template.provider_type

        # Get all enabled instances of the provider type
        instances = self._get_enabled_instances_by_type(provider_type)
        if not instances:
            raise ValueError(
                f"No enabled instances found for provider type '{provider_type}'")

        # Apply load balancing strategy
        selected_instance = self._apply_load_balancing_strategy(instances)

        self._logger.info(
            f"Selected load-balanced provider: {selected_instance.name} (type: {provider_type})"
        )

        return ProviderSelectionResult(
            provider_type=provider_type,
            provider_instance=selected_instance.name,
            selection_reason=f"Load balanced across { len(instances)} {provider_type} instances",
            confidence=0.9,
            alternatives=[inst.name for inst in instances if inst.name !=
                selected_instance.name],
        )

    def _select_by_api_capability(self, template: Template) -> ProviderSelectionResult:
        """Select provider based on API capability support."""
        provider_api = template.provider_api

        # Find providers that support the required API
        compatible_instances = self._find_compatible_providers(provider_api)
        if not compatible_instances:
            raise ValueError(f"No providers support API '{provider_api}'")

        # Select best instance (could be based on health, performance, etc.)
        selected_instance = self._select_best_compatible_instance(compatible_instances)

        self._logger.info(
            f"Selected capability-based provider: {selected_instance.name} for API: {provider_api}"
        )

        return ProviderSelectionResult(
            provider_type=selected_instance.type,
            provider_instance=selected_instance.name,
            selection_reason=f"Supports required API '{provider_api}'",
            confidence=0.8,
            alternatives=[
                inst.name for inst in compatible_instances if inst.name != selected_instance.name
            ],
        )

    def _select_default_provider(self, template: Template) -> ProviderSelectionResult:
        """Select default provider from configuration."""
        # Get default from configuration
        default_provider_type = getattr(
            self._provider_config, "default_provider_type", None)
        default_provider_instance = getattr(
            self._provider_config, "default_provider_instance", None
        )

        # If no defaults in config, use first enabled provider
        if not default_provider_instance:
            enabled_instances = [
                p for p in self._provider_config.providers if p.enabled]
            if not enabled_instances:
                raise ValueError("No enabled providers found in configuration")

            default_instance = enabled_instances[0]
            default_provider_type = default_instance.type
            default_provider_instance = default_instance.name

        self._logger.info(f"Selected default provider: {default_provider_instance}")

        return ProviderSelectionResult(
            provider_type=default_provider_type,
            provider_instance=default_provider_instance,
            selection_reason="Configuration default (no provider specified in template)",
            confidence=0.7,
        )

    def _get_provider_instance_config(self, provider_name: str) -> Optional[ProviderInstanceConfig]:
        """Get provider instance configuration by name."""
        for provider in self._provider_config.providers:
            if provider.name == provider_name:
                return provider
        return None

    def _get_enabled_instances_by_type(self, provider_type: str) -> List[ProviderInstanceConfig]:
        """Get all enabled provider instances of specified type."""
        return [
            provider
            for provider in self._provider_config.providers
            if provider.type == provider_type and provider.enabled
        ]

    def _apply_load_balancing_strategy(
        self, instances: List[ProviderInstanceConfig]
    ) -> ProviderInstanceConfig:
        """Apply load balancing strategy to select instance."""
        selection_policy = self._provider_config.selection_policy

        if selection_policy == "WEIGHTED_ROUND_ROBIN":
            return self._weighted_round_robin_selection(instances)
        elif selection_policy == "HEALTH_BASED":
            return self._health_based_selection(instances)
        elif selection_policy == "FIRST_AVAILABLE":
            return instances[0]  # First enabled instance
        else:
            # Default to highest priority (lowest priority number)
            return min(instances, key=lambda x: x.priority)

    def _weighted_round_robin_selection(
        self, instances: List[ProviderInstanceConfig]
    ) -> ProviderInstanceConfig:
        """Select instance using priority-first, then weighted selection."""
        # Sort by priority first (lower number = higher priority)
        sorted_instances = sorted(instances, key=lambda x: x.priority)

        # Get the highest priority (lowest number)
        highest_priority = sorted_instances[0].priority

        # Get all instances with the highest priority
        highest_priority_instances = [
            instance for instance in sorted_instances if instance.priority == highest_priority
        ]

        # If only one instance with highest priority, select it
        if len(highest_priority_instances) == 1:
            selected = highest_priority_instances[0]
            self._logger.debug(
                f"Selected provider { selected.name} (priority {selected.priority}, weight { selected.weight})"
            )
            return selected

        # If multiple instances with same priority, use weighted selection
        sum(instance.weight for instance in highest_priority_instances)

        # For now, select the one with highest weight among same priority
        # In production, this would maintain round-robin state
        selected = max(highest_priority_instances, key=lambda x: x.weight)
        self._logger.debug(
            f"Selected provider { selected.name} (priority {selected.priority}, weight {selected.weight}) from { len(highest_priority_instances)} candidates"
        )
        return selected

    def _health_based_selection(
        self, instances: List[ProviderInstanceConfig]
    ) -> ProviderInstanceConfig:
        """Select instance based on health status."""
        # For now, return highest priority healthy instance
        # In production, this would check actual health status
        return min(instances, key=lambda x: x.priority)

    def _find_compatible_providers(self, provider_api: str) -> List[ProviderInstanceConfig]:
        """Find provider instances that support the specified API."""
        compatible = []

        for provider in self._provider_config.providers:
            if not provider.enabled:
                continue

            # Check if provider supports the API
            if self._provider_supports_api(provider, provider_api):
                compatible.append(provider)

        return compatible

    def _provider_supports_api(self, provider: ProviderInstanceConfig, api: str) -> bool:
        """Check if provider instance supports the specified API."""
        # Get effective handlers for this provider
        provider_defaults = self._provider_config.provider_defaults.get(provider.type)
        effective_handlers = provider.get_effective_handlers(provider_defaults)

        # Check if the API is supported by any handler
        if api in effective_handlers:
            return True

        # For AWS providers, check against known APIs
        if provider.type == "aws":
            aws_apis = ["EC2Fleet", "SpotFleet", "RunInstances", "ASG"]
            return api in aws_apis

        # For other providers, assume support for now
        # In production, this would query actual provider capabilities
        return True

    def _select_best_compatible_instance(
        self, instances: List[ProviderInstanceConfig]
    ) -> ProviderInstanceConfig:
        """Select the best instance from compatible providers."""
        # Select based on priority (lower number = higher priority)
        return min(instances, key=lambda x: x.priority)

    def get_available_providers(self) -> List[Dict[str, Any]]:
        """Get list of all available provider instances with their status."""
        providers = []

        for provider in self._provider_config.providers:
            # Get effective handlers as capabilities
            provider_defaults = self._provider_config.provider_defaults.get(
                provider.type)
            effective_handlers = provider.get_effective_handlers(provider_defaults)
            capabilities = list(effective_handlers.keys())

            providers.append(
                {
                    "name": provider.name,
                    "type": provider.type,
                    "enabled": provider.enabled,
                    "priority": provider.priority,
                    "weight": provider.weight,
                    "capabilities": capabilities,
                }
            )

        return providers

    def validate_provider_selection(self, provider_type: str, provider_instance: str) -> bool:
        """Validate that a provider selection is valid."""
        provider_config = self._get_provider_instance_config(provider_instance)

        if not provider_config:
            return False

        if not provider_config.enabled:
            return False

        if provider_config.type != provider_type:
            return False

        return True
