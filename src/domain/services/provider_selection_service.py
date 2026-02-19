"""Provider Selection Domain Service - Business logic for provider selection."""

from typing import Any, Optional
from domain.base.ports import ConfigurationPort, LoggingPort
from domain.base.results import ProviderSelectionResult


class ProviderSelectionService:
    """Domain service for provider selection business logic."""

    def __init__(self, config: ConfigurationPort, logger: LoggingPort):
        self._config = config
        self._logger = logger
        self._provider_config = None
        self._active_provider_cache = None

    def select_provider_for_template(self, template: Any) -> Any:
        """
        Select provider instance for template requirements.

        Implements selection algorithm:
        1. CLI override (--provider flag)
        2. Explicit provider instance (template.provider_name)
        3. Provider type with load balancing (template.provider_type)
        4. Auto-selection based on API capabilities (template.provider_api)
        5. Fallback to configuration default
        """
        if self._logger:
            self._logger.info("Selecting provider for template: %s", template.template_id)

        # Strategy 1: CLI override (highest precedence)
        if self._config and (override := self._config.get_active_provider_override()):
            return self._select_override_provider(template, override)

        # Strategy 2: Explicit provider instance selection
        if template.provider_name:
            return self._select_explicit_provider(template)

        # Strategy 3: Provider type with load balancing
        if template.provider_type:
            return self._select_load_balanced_provider(template)

        # Strategy 4: Auto-selection based on API capabilities
        if template.provider_api:
            return self._select_api_capable_provider(template)

        # Strategy 5: Fallback to default
        return self._select_default_provider(template)

    def select_active_provider(self) -> Any:
        """Select active provider instance from configuration."""
        if self._active_provider_cache is not None:
            return self._active_provider_cache

        if self._logger:
            self._logger.debug("Selecting active provider using selection policy")

        if not self._provider_config:
            self._provider_config = self._config.get_provider_config() if self._config else None

        if not self._provider_config:
            raise ValueError("No provider configuration available")

        active_providers = self._provider_config.get_active_providers()
        if not active_providers:
            raise ValueError("No active providers found in configuration")

        if len(active_providers) == 1:
            selected = active_providers[0]
            reason = "single_active_provider"
        else:
            selected = self._apply_load_balancing_strategy(
                active_providers, self._provider_config.selection_policy
            )
            reason = f"load_balanced_{self._provider_config.selection_policy.lower()}"

        result = ProviderSelectionResult(
            provider_type=selected.type,
            provider_name=selected.name,
            selection_reason=reason,
            confidence=1.0,
            alternatives=[p.name for p in active_providers if p.name != selected.name],
        )

        self._active_provider_cache = result

        if self._logger:
            self._logger.info("Selected active provider: %s (%s)", selected.name, reason)

        return result

    def _select_override_provider(self, template: Any, provider_name: str) -> Any:
        """Select CLI-overridden provider with validation."""
        provider_instance = self._get_provider_instance_config(provider_name)
        if not provider_instance:
            raise ValueError(f"Provider instance '{provider_name}' not found")
        if not provider_instance.enabled:
            raise ValueError(f"Provider instance '{provider_name}' is disabled")

        return ProviderSelectionResult(
            provider_type=provider_instance.type,
            provider_name=provider_name,
            selection_reason=f"CLI override (--provider {provider_name})",
            confidence=1.0,
        )

    def _select_explicit_provider(self, template: Any) -> Any:
        """Select explicitly specified provider instance."""
        provider_name = template.provider_name
        provider_instance = self._get_provider_instance_config(provider_name)
        if not provider_instance:
            raise ValueError(f"Provider instance '{provider_name}' not found in configuration")
        if not provider_instance.enabled:
            raise ValueError(f"Provider instance '{provider_name}' is disabled")

        if self._logger:
            self._logger.info("Selected explicit provider: %s", provider_name)

        return ProviderSelectionResult(
            provider_type=provider_instance.type,
            provider_name=provider_name,
            selection_reason="Explicitly specified in template",
            confidence=1.0,
        )

    def _select_load_balanced_provider(self, template: Any) -> Any:
        """Select provider instance using load balancing within provider type."""
        provider_type = template.provider_type
        instances = self._get_enabled_instances_by_type(provider_type)
        if not instances:
            raise ValueError(f"No enabled instances found for provider type '{provider_type}'")

        selected_instance = self._apply_load_balancing_strategy(instances)

        if self._logger:
            self._logger.info(
                "Selected load-balanced provider: %s (type: %s)",
                selected_instance.name,
                provider_type,
            )

        return ProviderSelectionResult(
            provider_type=provider_type,
            provider_name=selected_instance.name,
            selection_reason=f"Load balanced across {len(instances)} {provider_type} instances",
            confidence=0.9,
            alternatives=[inst.name for inst in instances if inst.name != selected_instance.name],
        )

    def _select_api_capable_provider(self, template: Any) -> Any:
        """Select provider based on API capability support."""
        provider_api = template.provider_api
        compatible_instances = self._find_compatible_providers(provider_api)
        if not compatible_instances:
            raise ValueError(f"No providers support API '{provider_api}'")

        selected_instance = self._select_best_compatible_instance(compatible_instances)

        if self._logger:
            self._logger.info(
                "Selected capability-based provider: %s for API: %s",
                selected_instance.name,
                provider_api,
            )

        return ProviderSelectionResult(
            provider_type=selected_instance.type,
            provider_name=selected_instance.name,
            selection_reason=f"Supports required API '{provider_api}'",
            confidence=0.8,
            alternatives=[
                inst.name for inst in compatible_instances if inst.name != selected_instance.name
            ],
        )

    def _select_default_provider(self, template: Any) -> Any:
        """Select default provider from configuration."""
        if not self._provider_config:
            self._provider_config = self._config.get_provider_config() if self._config else None

        default_provider_type = getattr(self._provider_config, "default_provider_type", None)
        default_provider_instance = getattr(
            self._provider_config, "default_provider_instance", None
        )

        if not default_provider_instance:
            enabled_instances = [p for p in self._provider_config.providers if p.enabled]
            if not enabled_instances:
                raise ValueError("No enabled providers found in configuration")

            default_instance = enabled_instances[0]
            default_provider_type = default_instance.type
            default_provider_instance = default_instance.name

        if self._logger:
            self._logger.info("Selected default provider: %s", default_provider_instance)

        return ProviderSelectionResult(
            provider_type=default_provider_type,
            provider_name=default_provider_instance,
            selection_reason="Configuration default (no provider specified in template)",
            confidence=0.7,
        )

    def _get_provider_instance_config(self, provider_name: str) -> Optional[Any]:
        """Get provider instance configuration by name."""
        if not self._provider_config:
            self._provider_config = self._config.get_provider_config() if self._config else None
        if not self._provider_config:
            return None
        for provider in self._provider_config.providers:
            if provider.name == provider_name:
                return provider
        return None

    def _get_enabled_instances_by_type(self, provider_type: str) -> list[Any]:
        """Get all enabled provider instances of specified type."""
        if not self._provider_config:
            self._provider_config = self._config.get_provider_config() if self._config else None
        if not self._provider_config:
            return []
        return [
            provider
            for provider in self._provider_config.providers
            if provider.type == provider_type and provider.enabled
        ]

    def _apply_load_balancing_strategy(
        self, instances: list[Any], selection_policy: str = None
    ) -> Any:
        """Apply load balancing strategy to select instance."""
        if not selection_policy and self._provider_config:
            selection_policy = self._provider_config.selection_policy

        if selection_policy == "WEIGHTED_ROUND_ROBIN":
            return self._weighted_round_robin_selection(instances)
        elif selection_policy == "HEALTH_BASED":
            return self._health_based_selection(instances)
        elif selection_policy == "FIRST_AVAILABLE":
            return instances[0]
        else:
            return min(instances, key=lambda x: x.priority)

    def _weighted_round_robin_selection(self, instances: list[Any]) -> Any:
        """Select instance using priority-first, then weighted selection."""
        sorted_instances = sorted(instances, key=lambda x: x.priority)
        highest_priority = sorted_instances[0].priority
        highest_priority_instances = [
            instance for instance in sorted_instances if instance.priority == highest_priority
        ]

        if len(highest_priority_instances) == 1:
            selected = highest_priority_instances[0]
            if self._logger:
                self._logger.debug(
                    "Selected provider %s (priority %s, weight %s)",
                    selected.name,
                    selected.priority,
                    selected.weight,
                )
            return selected

        selected = max(highest_priority_instances, key=lambda x: x.weight)
        if self._logger:
            self._logger.debug(
                "Selected provider %s (priority %s, weight %s) from %s candidates",
                selected.name,
                selected.priority,
                selected.weight,
                len(highest_priority_instances),
            )
        return selected

    def _health_based_selection(self, instances: list[Any]) -> Any:
        """Select instance based on health status."""
        return min(instances, key=lambda x: x.priority)

    def _find_compatible_providers(self, provider_api: str) -> list[Any]:
        """Find provider instances that support the specified API."""
        if not self._provider_config:
            self._provider_config = self._config.get_provider_config() if self._config else None
        if not self._provider_config:
            return []

        compatible = []
        for provider in self._provider_config.providers:
            if not provider.enabled:
                continue
            if self._provider_supports_api(provider, provider_api):
                compatible.append(provider)
        return compatible

    def _provider_supports_api(self, provider: Any, api: str) -> bool:
        """Check if provider instance supports the specified API."""
        provider_defaults = self._provider_config.provider_defaults.get(provider.type)
        effective_handlers = provider.get_effective_handlers(provider_defaults)

        if not isinstance(effective_handlers, dict):
            effective_handlers = {}

        if api in effective_handlers:
            return True

        if provider.capabilities and api in provider.capabilities:
            return True

        if provider.type == "aws":
            aws_apis = ["EC2Fleet", "SpotFleet", "RunInstances", "ASG"]
            return api in aws_apis

        return True

    def _select_best_compatible_instance(self, instances: list[Any]) -> Any:
        """Select the best instance from compatible providers."""
        return min(instances, key=lambda x: x.priority)
