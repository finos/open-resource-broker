"""Provider Registry - Registry pattern for provider strategy factories."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional
import time
import threading
import importlib

from domain.base.exceptions import ConfigurationError
from domain.base.ports import LoggingPort
from monitoring.metrics import MetricsCollector

from infrastructure.registry.base_registry import BaseRegistration, BaseRegistry, RegistryMode


class UnsupportedProviderError(Exception):
    """Exception raised when an unsupported provider type is requested."""


class ProviderFactoryInterface(ABC):
    """Interface for provider factory functions."""

    @abstractmethod
    def create_strategy(self, config: Any) -> Any:
        """Create a provider strategy."""

    @abstractmethod
    def create_config(self, data: dict[str, Any]) -> Any:
        """Create a provider configuration."""


class ProviderRegistration(BaseRegistration):
    """Provider-specific registration with resolver and validator factories."""

    def __init__(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        resolver_factory: Optional[Callable] = None,
        validator_factory: Optional[Callable] = None,
    ) -> None:
        """Initialize the instance."""
        super().__init__(
            type_name,
            strategy_factory,
            config_factory,
            resolver_factory=resolver_factory,
            validator_factory=validator_factory,
        )
        self.resolver_factory = resolver_factory
        self.validator_factory = validator_factory


class ProviderRegistry(BaseRegistry):
    """
    Registry for provider strategy factories.

    Uses MULTI_CHOICE mode - multiple provider strategies simultaneously.
    Thread-safe singleton implementation using BaseRegistry.
    Handles strategy execution directly with on-demand creation.
    """

    def __init__(self) -> None:
        # Provider is MULTI_CHOICE - multiple provider strategies simultaneously
        super().__init__(mode=RegistryMode.MULTI_CHOICE)
        self._strategy_cache: dict[str, Any] = {}
        self._metrics: Optional[MetricsCollector] = None
        self._active_provider_cache: Optional[Any] = None

    async def execute_operation(self, provider_identifier: str, operation: Any, config: Any = None) -> Any:
        """
        Execute operation with provider strategy, creating strategy on-demand.
        
        Args:
            provider_identifier: Provider type or instance name
            operation: ProviderOperation to execute
            config: Optional provider configuration
            
        Returns:
            ProviderResult from strategy execution
        """
        self._ensure_dependencies_initialized()
        
        start_time = time.time()
        
        try:
            # Get or create strategy
            strategy = self.get_or_create_strategy(provider_identifier, config)
            if not strategy:
                return self._create_error_result(
                    f"Failed to create strategy for provider: {provider_identifier}",
                    "STRATEGY_CREATION_FAILED"
                )

            # Check capabilities
            capabilities = strategy.get_capabilities()
            if not capabilities.supports_operation(operation.operation_type):
                response_time_ms = (time.time() - start_time) * 1000
                if not self._metrics:
                    return
                op_base = f"provider.{provider_identifier}.{operation.operation_type.name.lower()}"
                self._metrics.increment_counter(f"{op_base}.error_total")
                self._metrics.record_time(f"{op_base}.duration", response_time_ms / 1000.0)
                
                return self._create_error_result(
                    f"Provider {provider_identifier} does not support operation {operation.operation_type}",
                    "OPERATION_NOT_SUPPORTED"
                )

            # Execute operation
            result = await strategy.execute_operation(operation)
            
            # Record metrics
            response_time_ms = (time.time() - start_time) * 1000
            if not self._metrics:
                return result
            op_base = f"provider.{provider_identifier}.{operation.operation_type.name.lower()}"
            if result.success:
                self._metrics.increment_counter(f"{op_base}.success_total")
            else:
                self._metrics.increment_counter(f"{op_base}.error_total")
            self._metrics.record_time(f"{op_base}.duration", response_time_ms / 1000.0)
            
            if self._logger_port:
                self._logger_port.debug(
                    "Operation %s executed by %s: success=%s, time=%.2fms",
                    operation.operation_type,
                    provider_identifier,
                    result.success,
                    response_time_ms,
                )
            
            return result

        except Exception as e:
            response_time_ms = (time.time() - start_time) * 1000
            if not self._metrics:
                return result
            op_base = f"provider.{provider_identifier}.{operation.operation_type.name.lower()}"
            self._metrics.increment_counter(f"{op_base}.error_total")
            self._metrics.record_time(f"{op_base}.duration", response_time_ms / 1000.0)
            
            if self._logger_port:
                self._logger_port.error(
                    "Error executing operation %s with %s: %s",
                    operation.operation_type,
                    provider_identifier,
                    e,
                )
            
            return self._create_error_result(
                f"Operation execution failed: {str(e)}",
                "EXECUTION_ERROR"
            )

    def get_strategy_capabilities(self, provider_identifier: str, config: Any = None) -> Optional[Any]:
        """Get capabilities for a provider strategy."""
        try:
            strategy = self.get_or_create_strategy(provider_identifier, config)
            return strategy.get_capabilities() if strategy else None
        except Exception as e:
            if self._logger_port:
                self._logger_port.error("Error getting capabilities for %s: %s", provider_identifier, e)
            return None

    def check_strategy_health(self, provider_identifier: str, config: Any = None) -> Optional[Any]:
        """Check health of a provider strategy."""
        try:
            strategy = self.get_or_create_strategy(provider_identifier, config)
            if not strategy:
                return None
                
            health_status = strategy.check_health()
            if self._metrics:
                self._metrics.increment_counter("provider_strategy_health_checks_total", 1.0)
            return health_status
            
        except Exception as e:
            if self._logger_port:
                self._logger_port.error("Error checking health of %s: %s", provider_identifier, e)
            return self._create_unhealthy_status(f"Health check failed: {str(e)}")

    def get_or_create_strategy(self, provider_identifier: str, config: Any = None) -> Optional[Any]:
        """
        Get cached strategy or create new one with auto-registration.
        
        Args:
            provider_identifier: Provider type or instance name
            config: Configuration (ProviderInstanceConfig object or dict)
            
        Returns:
            Strategy instance or None if creation failed
        """
        # Check cache first
        if provider_identifier in self._strategy_cache:
            return self._strategy_cache[provider_identifier]

        # Auto-register if needed
        if config and hasattr(config, 'name') and hasattr(config, 'type'):
            # It's a ProviderInstanceConfig object - register the instance
            self.ensure_provider_instance_registered_from_config(config)
        elif not self.is_provider_instance_registered(provider_identifier) and not self.is_provider_registered(provider_identifier):
            # Try to auto-register the provider type
            provider_type = provider_identifier.split('_')[0] if '_' in provider_identifier else provider_identifier
            self.ensure_provider_type_registered(provider_type)

        # Create new strategy
        strategy = None
        
        # Try instance creation first
        if self.is_provider_instance_registered(provider_identifier):
            # If no config provided, get the stored provider instance config
            if config is None:
                # Get the provider instance config from configuration manager
                try:
                    from infrastructure.di.container import get_container
                    from domain.base.ports.configuration_port import ConfigurationPort
                    container = get_container()
                    config_port = container.get(ConfigurationPort)
                    provider_config = config_port.get_provider_config()
                    
                    if provider_config:
                        for instance in provider_config.get_active_providers():
                            if instance.name == provider_identifier:
                                config = instance
                                break
                except Exception as e:
                    if self._logger_port:
                        self._logger_port.warning("Failed to retrieve provider instance config for %s: %s", provider_identifier, e)
            
            strategy = self.create_strategy_by_instance(provider_identifier, config)
        # Fall back to type creation
        elif self.is_provider_registered(provider_identifier):
            strategy = self.create_strategy_by_type(provider_identifier, config)
        
        if strategy:
            # Initialize strategy
            if hasattr(strategy, 'initialize') and not strategy.is_initialized:
                if not strategy.initialize():
                    if self._logger_port:
                        self._logger_port.error("Failed to initialize strategy: %s", provider_identifier)
                    return None
            
            # Cache strategy
            self._strategy_cache[provider_identifier] = strategy
            
        return strategy

    def _create_error_result(self, message: str, code: str) -> Any:
        """Create error result using ProviderResult."""
        try:
            from providers.base.strategy.provider_strategy import ProviderResult
            return ProviderResult.error_result(message, code)
        except ImportError:
            # Fallback if ProviderResult not available
            return {"success": False, "error_message": message, "error_code": code}

    def _create_unhealthy_status(self, message: str) -> Any:
        """Create unhealthy status."""
        try:
            from providers.base.strategy.provider_strategy import ProviderHealthStatus
            return ProviderHealthStatus.unhealthy(message, {})
        except ImportError:
            return {"healthy": False, "message": message}

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
        if self._logger_port:
            self._logger_port.info("Selecting provider for template: %s", template.template_id)

        # Strategy 1: CLI override (highest precedence)
        if self._config_port and (override := self._config_port.get_active_provider_override()):
            return self._select_override_provider(template, override)

        # Strategy 2: Explicit provider instance selection
        if template.provider_name:
            return self._select_explicit_provider(template)

        # Strategy 3: Provider type with load balancing
        if template.provider_type:
            return self._select_load_balanced_provider(template)

        # Strategy 4: Auto-selection based on API capabilities
        if template.provider_api:
            return self._select_by_api_capability(template)

        # Strategy 5: Fallback to default
        return self._select_default_provider(template)

    def _ensure_provider_dependencies(self) -> None:
        """Ensure provider-specific dependencies are initialized."""
        self._ensure_dependencies_initialized()
        
        if not hasattr(self, '_provider_config') or not self._provider_config:
            self._provider_config = self._config_port.get_provider_config() if self._config_port else None
            
        if not self._metrics:
            self._metrics = MetricsCollector(config={"METRICS_ENABLED": True})

    def select_active_provider(self) -> Any:
        """Select active provider instance from configuration."""
        self._ensure_provider_dependencies()
        
        if self._active_provider_cache is not None:
            return self._active_provider_cache

        if self._logger_port:
            self._logger_port.debug("Selecting active provider using selection policy")

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

        from providers.results import ProviderSelectionResult
        result = ProviderSelectionResult(
            provider_type=selected.type,
            provider_name=selected.name,
            selection_reason=reason,
            confidence=1.0,
            alternatives=[p.name for p in active_providers if p.name != selected.name],
        )

        self._active_provider_cache = result

        if self._logger_port:
            self._logger_port.info("Selected active provider: %s (%s)", selected.name, reason)

        return result

    def validate_template_requirements(
        self, 
        template: Any, 
        provider_instance: str,
        validation_level: Any = None
    ) -> Any:
        """
        Validate template requirements against provider capabilities.
        
        Performs comprehensive validation of template requirements
        against the specified provider's capabilities.
        """
        self._ensure_provider_dependencies()
        
        # Get provider config for lazy registration
        provider_config = None
        try:
            provider_config_root = self._config_port.get_provider_config()
            for instance in provider_config_root.get_active_providers():
                if instance.name == provider_instance:
                    provider_config = instance
                    break
        except Exception:
            pass
        
        # Trigger lazy registration before validation
        strategy = self.get_or_create_strategy(provider_instance, provider_config)
        
        if self._logger_port:
            self._logger_port.info(
                "Validating template %s against provider %s",
                template.template_id,
                provider_instance,
            )

        from providers.results import ValidationResult, ValidationLevel
        if validation_level is None:
            validation_level = ValidationLevel.STRICT

        result = ValidationResult(
            is_valid=True,
            provider_instance=provider_instance,
            errors=[],
            warnings=[],
            supported_features=[],
            unsupported_features=[],
        )

        try:
            # Use config-based capabilities directly (they have correct supported_apis)
            capabilities = self._get_config_based_capabilities(provider_instance)

            self._validate_api_support(template, capabilities, result)
            self._validate_pricing_model(template, capabilities, result)
            self._validate_fleet_type_support(template, capabilities, result)
            self._validate_instance_limits(template, capabilities, result)

            if validation_level == ValidationLevel.STRICT and result.warnings:
                result.errors.extend(result.warnings)
                result.warnings = []
                result.is_valid = False
            elif validation_level == ValidationLevel.PERMISSIVE:
                result.warnings = []

            result.is_valid = len(result.errors) == 0

            if self._logger_port:
                self._logger_port.info(
                    "Validation result for %s: %s",
                    template.template_id,
                    "VALID" if result.is_valid else "INVALID",
                )

        except Exception as e:
            if self._logger_port:
                self._logger_port.error("Validation failed with exception: %s", str(e))
            result.is_valid = False
            result.errors.append(f"Validation error: {e!s}")

        return result

    def _select_override_provider(self, template: Any, provider_name: str) -> Any:
        """Select CLI-overridden provider with validation."""
        provider_instance = self._get_provider_instance_config(provider_name)
        if not provider_instance:
            raise ValueError(f"Provider instance '{provider_name}' not found")
        if not provider_instance.enabled:
            raise ValueError(f"Provider instance '{provider_name}' is disabled")

        from providers.results import ProviderSelectionResult
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

        if self._logger_port:
            self._logger_port.info("Selected explicit provider: %s", provider_name)

        from providers.results import ProviderSelectionResult
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

        if self._logger_port:
            self._logger_port.info(
                "Selected load-balanced provider: %s (type: %s)",
                selected_instance.name,
                provider_type,
            )

        from providers.results import ProviderSelectionResult
        return ProviderSelectionResult(
            provider_type=provider_type,
            provider_name=selected_instance.name,
            selection_reason=f"Load balanced across {len(instances)} {provider_type} instances",
            confidence=0.9,
            alternatives=[inst.name for inst in instances if inst.name != selected_instance.name],
        )

    def _select_by_api_capability(self, template: Any) -> Any:
        """Select provider based on API capability support."""
        provider_api = template.provider_api
        compatible_instances = self._find_compatible_providers(provider_api)
        if not compatible_instances:
            raise ValueError(f"No providers support API '{provider_api}'")

        selected_instance = self._select_best_compatible_instance(compatible_instances)

        if self._logger_port:
            self._logger_port.info(
                "Selected capability-based provider: %s for API: %s",
                selected_instance.name,
                provider_api,
            )

        from providers.results import ProviderSelectionResult
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

        if self._logger_port:
            self._logger_port.info("Selected default provider: %s", default_provider_instance)

        from providers.results import ProviderSelectionResult
        return ProviderSelectionResult(
            provider_type=default_provider_type,
            provider_name=default_provider_instance,
            selection_reason="Configuration default (no provider specified in template)",
            confidence=0.7,
        )

    def _get_provider_instance_config(self, provider_name: str) -> Optional[Any]:
        """Get provider instance configuration by name."""
        if not self._provider_config:
            return None
        for provider in self._provider_config.providers:
            if provider.name == provider_name:
                return provider
        return None

    def _get_enabled_instances_by_type(self, provider_type: str) -> list[Any]:
        """Get all enabled provider instances of specified type."""
        if not self._provider_config:
            return []
        return [
            provider
            for provider in self._provider_config.providers
            if provider.type == provider_type and provider.enabled
        ]

    def _apply_load_balancing_strategy(self, instances: list[Any], selection_policy: str = None) -> Any:
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
            if self._logger_port:
                self._logger_port.debug(
                    "Selected provider %s (priority %s, weight %s)",
                    selected.name,
                    selected.priority,
                    selected.weight,
                )
            return selected

        selected = max(highest_priority_instances, key=lambda x: x.weight)
        if self._logger_port:
            self._logger_port.debug(
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

    def _get_provider_capabilities_for_validation(self, provider_instance: str) -> Optional[Any]:
        """Get capabilities for specified provider instance."""
        try:
            if not self.is_instance_registered(provider_instance):
                if self._config_port:
                    provider_config = self._config_port.get_provider_instance_config(provider_instance)
                    if provider_config:
                        self.ensure_provider_instance_registered_from_config(provider_config)
            
            if self._config_port:
                provider_config = self._config_port.get_provider_instance_config(provider_instance)
                if not provider_config:
                    if self._logger_port:
                        self._logger_port.warning("Provider instance '%s' not found in configuration", provider_instance)
                    return None
                
                strategy = self.get_or_create_strategy(provider_instance, provider_config)
                return strategy.get_capabilities()
        except Exception as e:
            if self._logger_port:
                self._logger_port.warning("Failed to get capabilities for %s: %s", provider_instance, str(e))
            return None

    def _get_config_based_capabilities(self, provider_instance: str) -> Any:
        """Get capabilities from merged provider configuration."""
        if not self._config_port:
            raise ValueError("No configuration manager available")
            
        provider_config = self._config_port.get_provider_instance_config(provider_instance)
        if not provider_config:
            raise ValueError(f"Provider instance {provider_instance} not found in configuration")
        
        provider_config_root = self._config_port.get_provider_config()
        provider_defaults = provider_config_root.provider_defaults.get(provider_config.type)
        effective_handlers = provider_config.get_effective_handlers(provider_defaults)
        supported_apis = list(effective_handlers.keys())
        
        from providers.base.strategy.provider_strategy import ProviderCapabilities, ProviderOperationType
        return ProviderCapabilities(
            provider_type=provider_config.type,
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
            ],
            supported_apis=supported_apis,
            features={},
        )

    def _validate_api_support(self, template: Any, capabilities: Any, result: Any) -> None:
        """Validate that provider supports the required API."""
        if not template.provider_api:
            result.warnings.append("No provider API specified in template")
            return

        try:
            supported_apis = capabilities.supported_apis
            if template.provider_api not in supported_apis:
                result.errors.append(
                    f"Provider does not support API '{template.provider_api}'. Supported APIs: {supported_apis}"
                )
            else:
                result.supported_features.append(f"API: {template.provider_api}")
        except Exception as e:
            if self._logger_port:
                self._logger_port.error("Error in API validation: %s", e)
            result.errors.append(f"API validation error: {e}")

    def _validate_pricing_model(self, template: Any, capabilities: Any, result: Any) -> None:
        """Validate pricing model support (spot/on-demand)."""
        if not template.provider_api:
            return

        api_capabilities = capabilities.get_feature("api_capabilities", {})
        api_caps = api_capabilities.get(template.provider_api, {})
        price_type = getattr(template, "price_type", "ondemand")

        if price_type == "spot":
            if not api_caps.get("supports_spot", False):
                result.errors.append(
                    f"API '{template.provider_api}' does not support spot instances"
                )
            else:
                result.supported_features.append("Pricing: Spot instances")
        elif price_type == "ondemand":
            if not api_caps.get("supports_on_demand", True):
                result.errors.append(
                    f"API '{template.provider_api}' does not support on-demand instances"
                )
            else:
                result.supported_features.append("Pricing: On-demand instances")

    def _validate_fleet_type_support(self, template: Any, capabilities: Any, result: Any) -> None:
        """Validate fleet type support."""
        if not template.provider_api:
            return

        fleet_type = getattr(template, "fleet_type", None)
        if not fleet_type:
            fleet_type = template.metadata.get("fleet_type") if template.metadata else None

        if not fleet_type:
            return

        api_capabilities = capabilities.get_feature("api_capabilities", {})
        api_caps = api_capabilities.get(template.provider_api, {})
        supported_fleet_types = api_caps.get("supported_fleet_types", [])

        if supported_fleet_types and fleet_type not in supported_fleet_types:
            result.errors.append(
                f"API '{template.provider_api}' does not support fleet type '{fleet_type}'. Supported types: {supported_fleet_types}"
            )
        elif supported_fleet_types:
            result.supported_features.append(f"Fleet type: {fleet_type}")

    def _validate_instance_limits(self, template: Any, capabilities: Any, result: Any) -> None:
        """Validate instance count limits."""
        if not template.provider_api:
            return

        api_capabilities = capabilities.get_feature("api_capabilities", {})
        api_caps = api_capabilities.get(template.provider_api, {})
        max_instances = api_caps.get("max_instances", float("inf"))

        if template.max_instances > max_instances:
            result.errors.append(
                f"Requested {template.max_instances} instances exceeds API limit of {max_instances}"
            )
        else:
            result.supported_features.append(
                f"Instance count: {template.max_instances} (within limit)"
            )





    # Convenience methods for common operations
    async def create_machines(
        self, 
        provider_identifier: str, 
        template_id: str, 
        count: int, 
        config: Any = None,
        **kwargs
    ) -> Any:
        """Create machines using provider strategy."""
        from providers.base.strategy.provider_strategy import ProviderOperation, ProviderOperationType
        
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_id": template_id,
                "count": count,
                **kwargs
            }
        )
        return await self.execute_operation(provider_identifier, operation, config)

    async def terminate_machines(
        self, 
        provider_identifier: str, 
        machine_ids: list[str], 
        config: Any = None,
        **kwargs
    ) -> Any:
        """Terminate machines using provider strategy."""
        from providers.base.strategy.provider_strategy import ProviderOperation, ProviderOperationType
        
        operation = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "machine_ids": machine_ids,
                **kwargs
            }
        )
        return await self.execute_operation(provider_identifier, operation, config)

    async def get_machine_status(
        self, 
        provider_identifier: str, 
        machine_ids: list[str], 
        config: Any = None,
        **kwargs
    ) -> Any:
        """Get machine status using provider strategy."""
        from providers.base.strategy.provider_strategy import ProviderOperation, ProviderOperationType
        
        operation = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "machine_ids": machine_ids,
                **kwargs
            }
        )
        return await self.execute_operation(provider_identifier, operation, config)

    async def validate_template(
        self, 
        provider_identifier: str, 
        template: dict, 
        config: Any = None,
        **kwargs
    ) -> Any:
        """Validate template using provider strategy."""
        from providers.base.strategy.provider_strategy import ProviderOperation, ProviderOperationType
        
        operation = ProviderOperation(
            operation_type=ProviderOperationType.VALIDATE_TEMPLATE,
            parameters={
                "template": template,
                **kwargs
            }
        )
        return await self.execute_operation(provider_identifier, operation, config)

    def ensure_provider_type_registered(self, provider_type: str) -> bool:
        """
        Ensure provider type is registered by dynamically importing and registering if needed.
        
        Args:
            provider_type: Type identifier for the provider (e.g., 'aws', 'azure')
            
        Returns:
            True if provider is registered (was already or just registered), False if failed
        """
        # Check if already registered
        if self.is_provider_registered(provider_type):
            if self._logger_port:
                self._logger_port.debug("Provider type '%s' already registered", provider_type)
            return True
        
        # Try to dynamically import and register
        try:
            if self._logger_port:
                self._logger_port.debug("Attempting to register provider type: %s", provider_type)
            
            # Import the provider's registration module
            module_name = f"providers.{provider_type}.registration"
            registration_module = importlib.import_module(module_name)
            
            # Call the provider's registration function
            register_function_name = f"register_{provider_type}_provider"
            if hasattr(registration_module, register_function_name):
                register_function = getattr(registration_module, register_function_name)
                register_function(self, self._logger_port)
                
                if self._logger_port:
                    self._logger_port.info("Successfully registered provider type: %s", provider_type)
                return True
            else:
                if self._logger_port:
                    self._logger_port.warning(
                        "Provider registration function '%s' not found in module '%s'",
                        register_function_name,
                        module_name,
                    )
                return False
                
        except ImportError as e:
            if self._logger_port:
                self._logger_port.warning("Failed to import provider registration module '%s': %s", module_name, e)
            return False
        except Exception as e:
            if self._logger_port:
                self._logger_port.error("Error registering provider type '%s': %s", provider_type, e)
            return False

    def ensure_provider_instance_registered_from_config(self, provider_instance) -> bool:
        """
        Ensure provider instance is registered from config.
        Handles both type and instance registration.
        
        Args:
            provider_instance: ProviderInstanceConfig object
            
        Returns:
            True if registered successfully, False otherwise
        """
        # Already registered?
        if self.is_provider_instance_registered(provider_instance.name):
            if self._logger_port:
                self._logger_port.debug("Provider instance '%s' already registered", provider_instance.name)
            return True
        
        try:
            import importlib
            provider_type = provider_instance.type
            
            if self._logger_port:
                self._logger_port.debug("Registering provider instance: %s", provider_instance.name)
            
            # Dynamically import provider registration module
            module = importlib.import_module(f'providers.{provider_type}.registration')
            
            # Call provider's instance registration function
            register_func = getattr(module, f'register_{provider_type}_provider_instance')
            register_func(provider_instance, self._logger_port)
            
            if self._logger_port:
                self._logger_port.info("Successfully registered provider instance: %s", provider_instance.name)
            return True
        except (ImportError, AttributeError) as e:
            if self._logger_port:
                self._logger_port.warning(f"Failed to register provider instance '{provider_instance.name}': {e}")
            return False

    def register(
        self,
        provider_type: str,
        strategy_factory: Callable,
        config_factory: Callable,
        resolver_factory: Optional[Callable] = None,
        validator_factory: Optional[Callable] = None,
    ) -> None:
        """Register provider type - implements abstract method."""
        try:
            self.register_type(
                provider_type,
                strategy_factory,
                config_factory,
                resolver_factory=resolver_factory,
                validator_factory=validator_factory,
            )
        except ValueError as e:
            raise ConfigurationError(str(e))

    def register_provider(
        self,
        provider_type: str,
        strategy_factory: Callable,
        config_factory: Callable,
        resolver_factory: Optional[Callable] = None,
        validator_factory: Optional[Callable] = None,
    ) -> None:
        """
        Register a provider with its factory functions - backward compatibility method.

        Args:
            provider_type: Type identifier for the provider (e.g., 'aws', 'provider1')
            strategy_factory: Factory function to create provider strategy
            config_factory: Factory function to create provider configuration
            resolver_factory: Optional factory for template resolver
            validator_factory: Optional factory for template validator

        Raises:
            ValueError: If provider_type is already registered
        """
        self.register(
            provider_type,
            strategy_factory,
            config_factory,
            resolver_factory,
            validator_factory,
        )

    def register_provider_instance(
        self,
        provider_type: str,
        instance_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        resolver_factory: Optional[Callable] = None,
        validator_factory: Optional[Callable] = None,
    ) -> None:
        """
        Register a named provider instance with its factory functions.

        Args:
            provider_type: Type identifier for the provider (e.g., 'aws')
            instance_name: Unique name for this provider instance (e.g., 'aws-us-east-1')
            strategy_factory: Factory function to create provider strategy
            config_factory: Factory function to create provider configuration
            resolver_factory: Optional factory for template resolver
            validator_factory: Optional factory for template validator

        Raises:
            ValueError: If instance_name is already registered
        """
        try:
            self.register_instance(
                provider_type,
                instance_name,
                strategy_factory,
                config_factory,
                resolver_factory=resolver_factory,
                validator_factory=validator_factory,
            )
        except ValueError:
            raise ValueError(f"Provider instance '{instance_name}' is already registered")

    def create_strategy(self, provider_type: str, config: Any) -> Any:
        """Create strategy - implements abstract method by delegating to cached method."""
        return self.get_or_create_strategy(provider_type, config)

    def create_config(self, provider_type: str, data: dict[str, Any]) -> Any:
        """
        Create a provider configuration using registered factory.

        Args:
            provider_type: Type identifier for the provider
            data: Configuration data dictionary

        Returns:
            Created provider configuration instance

        Raises:
            UnsupportedProviderError: If provider type is not registered
        """
        try:
            registration = self._get_type_registration(provider_type)
            config = registration.config_factory(data)
            self.logger.debug("Created config for provider: %s", provider_type)
            return config
        except ValueError:
            available_providers = ", ".join(self.get_registered_types())
            raise UnsupportedProviderError(
                f"Provider type '{provider_type}' is not registered. "
                f"Available providers: {available_providers}"
            )
        except Exception as e:
            raise ConfigurationError(
                f"Failed to create config for provider '{provider_type}': {e!s}"
            )

    def create_resolver(self, provider_type: str) -> Optional[Any]:
        """
        Create a template resolver using registered factory.

        Args:
            provider_type: Type identifier for the provider

        Returns:
            Created template resolver instance or None if not registered
        """
        return self.create_additional_component(provider_type, "resolver_factory")

    def create_validator(self, provider_type: str) -> Optional[Any]:
        """
        Create a template validator using registered factory.

        Args:
            provider_type: Type identifier for the provider

        Returns:
            Created template validator instance or None if not registered
        """
        return self.create_additional_component(provider_type, "validator_factory")

    def unregister_provider(self, provider_type: str) -> bool:
        """
        Unregister a provider - backward compatibility method.

        Args:
            provider_type: Type identifier for the provider

        Returns:
            True if provider was unregistered, False if not found
        """
        return self.unregister_type(provider_type)

    def unregister_provider_instance(self, instance_name: str) -> bool:
        """
        Unregister a named provider instance.

        Args:
            instance_name: Name of the provider instance

        Returns:
            True if instance was unregistered, False if not found
        """
        return self.unregister_instance(instance_name)

    def is_provider_registered(self, provider_type: str) -> bool:
        """
        Check if a provider type is registered - backward compatibility method.

        Args:
            provider_type: Type identifier for the provider

        Returns:
            True if provider is registered, False otherwise
        """
        return self.is_registered(provider_type)

    def is_provider_instance_registered(self, instance_name: str) -> bool:
        """
        Check if a provider instance is registered.

        Args:
            instance_name: Name of the provider instance

        Returns:
            True if instance is registered, False otherwise
        """
        return self.is_instance_registered(instance_name)

    def get_registered_providers(self) -> list[str]:
        """
        Get list of all registered provider types - backward compatibility method.

        Returns:
            List of registered provider type identifiers
        """
        return self.get_registered_types()

    def get_registered_provider_instances(self) -> list[str]:
        """
        Get list of all registered provider instance names.

        Returns:
            List of registered provider instance names
        """
        return self.get_registered_instances()

    def get_provider_instance_registration(
        self, instance_name: str
    ) -> Optional[ProviderRegistration]:
        """
        Get registration for a specific provider instance.

        Args:
            instance_name: Name of the provider instance

        Returns:
            ProviderRegistration if found, None otherwise
        """
        try:
            return self._get_instance_registration(instance_name)
        except ValueError:
            return None

    def _create_registration(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **additional_factories,
    ) -> BaseRegistration:
        """Create provider-specific registration."""
        return ProviderRegistration(
            type_name,
            strategy_factory,
            config_factory,
            additional_factories.get("resolver_factory"),
            additional_factories.get("validator_factory"),
        )


# Global registry instance
_provider_registry_instance: Optional[ProviderRegistry] = None
_registry_lock = threading.Lock()


def get_provider_registry() -> ProviderRegistry:
    """Get the singleton provider registry instance."""
    global _provider_registry_instance
    
    if _provider_registry_instance is None:
        with _registry_lock:
            if _provider_registry_instance is None:
                _provider_registry_instance = ProviderRegistry()
    
    return _provider_registry_instance
