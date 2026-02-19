"""Provider Registry - Registry pattern for provider strategy factories."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, List
import threading
import importlib

from domain.base.exceptions import ConfigurationError

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
    """

    def __init__(self) -> None:
        # Provider is MULTI_CHOICE - multiple provider strategies simultaneously
        super().__init__(mode=RegistryMode.MULTI_CHOICE)
        self._strategy_cache: dict[str, Any] = {}
        # Create logger directly since BaseRegistry no longer provides it
        from infrastructure.logging.logger import get_logger

        self._logger = get_logger(__name__)

    def get_strategy(self, provider_identifier: str) -> Optional[Any]:
        """Get cached strategy instance."""
        return self._strategy_cache.get(provider_identifier)

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
        if config and hasattr(config, "name") and hasattr(config, "type"):
            # It's a ProviderInstanceConfig object - register the instance
            self.ensure_provider_instance_registered_from_config(config)
        elif not self.is_provider_instance_registered(
            provider_identifier
        ) and not self.is_provider_registered(provider_identifier):
            # Try to auto-register the provider type
            provider_type = (
                provider_identifier.split("_")[0]
                if "_" in provider_identifier
                else provider_identifier
            )
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
                    if self._logger:
                        self._logger.warning(
                            "Failed to retrieve provider instance config for %s: %s",
                            provider_identifier,
                            e,
                        )

            strategy = self.create_strategy_by_instance(provider_identifier, config)
        # Fall back to type creation
        elif self.is_provider_registered(provider_identifier):
            strategy = self.create_strategy_by_type(provider_identifier, config)

        if strategy:
            # Initialize strategy
            if hasattr(strategy, "initialize") and not strategy.is_initialized:
                if not strategy.initialize():
                    if self._logger:
                        self._logger.error("Failed to initialize strategy: %s", provider_identifier)
                    return None

            # Cache strategy
            self._strategy_cache[provider_identifier] = strategy

        return strategy

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
            if self._logger:
                self._logger.debug("Provider type '%s' already registered", provider_type)
            return True

        # Try to dynamically import and register
        try:
            if self._logger:
                self._logger.debug("Attempting to register provider type: %s", provider_type)

            # Import the provider's registration module
            module_name = f"providers.{provider_type}.registration"
            registration_module = importlib.import_module(module_name)

            # Call the provider's registration function
            register_function_name = f"register_{provider_type}_provider"
            if hasattr(registration_module, register_function_name):
                register_function = getattr(registration_module, register_function_name)
                register_function(self, self._logger)

                if self._logger:
                    self._logger.info("Successfully registered provider type: %s", provider_type)
                return True
            else:
                if self._logger:
                    self._logger.warning(
                        "Provider registration function '%s' not found in module '%s'",
                        register_function_name,
                        module_name,
                    )
                return False

        except ImportError as e:
            if self._logger:
                self._logger.warning(
                    "Failed to import provider registration module '%s': %s", module_name, e
                )
            return False
        except Exception as e:
            if self._logger:
                self._logger.error("Error registering provider type '%s': %s", provider_type, e)
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
            if self._logger:
                self._logger.debug(
                    "Provider instance '%s' already registered", provider_instance.name
                )
            return True

        try:
            import importlib

            provider_type = provider_instance.type

            if self._logger:
                self._logger.debug("Registering provider instance: %s", provider_instance.name)

            # Dynamically import provider registration module
            module = importlib.import_module(f"providers.{provider_type}.registration")

            # Call provider's instance registration function
            register_func = getattr(module, f"register_{provider_type}_provider_instance")
            register_func(provider_instance, self._logger)

            if self._logger:
                self._logger.info(
                    "Successfully registered provider instance: %s", provider_instance.name
                )
            return True
        except (ImportError, AttributeError) as e:
            if self._logger:
                self._logger.warning(
                    f"Failed to register provider instance '{provider_instance.name}': {e}"
                )
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
            if self._logger:
                self._logger.debug("Created config for provider: %s", provider_type)
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

    def _provider_supports_capabilities(self, strategy: Any, capabilities: List[str]) -> bool:
        """Check if provider strategy supports required capabilities."""
        if not capabilities:
            return True

        provider_capabilities = getattr(strategy, "supported_capabilities", [])
        return all(cap in provider_capabilities for cap in capabilities)

    # ============================================================================
    # PROVIDER SELECTION LOGIC
    # ============================================================================
    # CRITICAL ARCHITECTURE NOTE:
    #
    # Provider selection logic is implemented in this registry class
    # (infrastructure layer) rather than a separate domain service because:
    #
    # 1. It requires access to configuration (infrastructure dependency)
    # 2. It requires access to provider instances (managed by registry)
    # 3. Moving to domain layer creates circular dependencies:
    #    Domain Service → ConfigurationPort → Registry → Domain Service
    #
    # This is architecturally correct:
    # - Registry pattern allows selection logic in the registry
    # - Infrastructure layer can access configuration
    # - No circular dependencies (infrastructure is outermost layer)
    #
    # DO NOT MOVE THIS TO DOMAIN LAYER
    # See: .kiro/backlog/cli-hanging-circular-dependency.md
    # ============================================================================

    def select_provider_for_template(
        self, template: Any, provider_name: Optional[str] = None, logger: Optional[Any] = None
    ) -> Any:
        """Select provider instance for template requirements.

        Selection hierarchy:
        1. CLI override (--provider flag)
        2. Explicit provider instance (template.provider_name)
        3. Provider type with load balancing (template.provider_type)
        4. Auto-selection based on API capabilities (template.provider_api)
        5. Fallback to configuration default

        ARCHITECTURE NOTE: This logic is in the registry (infrastructure layer)
        because it requires access to configuration and provider instances.
        DO NOT move to domain layer - it creates circular dependencies.
        """
        # Import handled in individual methods where needed

        if logger:
            logger.info(
                "Selecting provider for template: %s", getattr(template, "template_id", "unknown")
            )

        # Strategy 1: CLI override (highest precedence)
        if provider_name or self._get_cli_override():
            return self._select_by_cli_override(
                template, provider_name or self._get_cli_override(), logger
            )

        # Strategy 2: Explicit provider instance selection
        if hasattr(template, "provider_name") and template.provider_name:
            return self._select_by_explicit_provider(template, logger)

        # Strategy 3: Provider type with load balancing
        if hasattr(template, "provider_type") and template.provider_type:
            return self._select_by_provider_type(template, logger)

        # Strategy 4: Auto-selection based on API capabilities
        if hasattr(template, "provider_api") and template.provider_api:
            return self._select_by_api_capability(template, logger)

        # Strategy 5: Fallback to default
        return self._select_default_provider(template, logger)

    def select_active_provider(self, logger: Optional[Any] = None) -> Any:
        """Select active provider instance from configuration."""
        try:
            from domain.base.results import ProviderSelectionResult
        except ImportError:
            # Fallback if import fails
            pass

        if logger:
            logger.debug("Selecting active provider using selection policy")

        provider_config = self._get_provider_config()
        if not provider_config:
            raise ValueError("No provider configuration available")

        active_providers = provider_config.get_active_providers()
        if not active_providers:
            raise ValueError("No active providers found in configuration")

        if len(active_providers) == 1:
            selected = active_providers[0]
            reason = "single_active_provider"
        else:
            selected = self._apply_load_balancing_strategy(
                active_providers, provider_config.selection_policy
            )
            reason = f"load_balanced_{provider_config.selection_policy.lower()}"

        result = ProviderSelectionResult(
            provider_type=selected.type,
            provider_name=selected.name,
            selection_reason=reason,
            confidence=1.0,
            alternatives=[p.name for p in active_providers if p.name != selected.name],
        )

        if logger:
            logger.info("Selected active provider: %s (%s)", selected.name, reason)

        return result

    def _select_by_cli_override(
        self, template: Any, provider_name: str, logger: Optional[Any]
    ) -> Any:
        """Select CLI-overridden provider with validation."""
        try:
            from domain.base.results import ProviderSelectionResult
        except ImportError:
            pass

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

    def _select_by_explicit_provider(self, template: Any, logger: Optional[Any]) -> Any:
        """Select explicitly specified provider instance."""
        try:
            from domain.base.results import ProviderSelectionResult
        except ImportError:
            pass

        provider_name = template.provider_name
        provider_instance = self._get_provider_instance_config(provider_name)
        if not provider_instance:
            raise ValueError(f"Provider instance '{provider_name}' not found in configuration")
        if not provider_instance.enabled:
            raise ValueError(f"Provider instance '{provider_name}' is disabled")

        if logger:
            logger.info("Selected explicit provider: %s", provider_name)

        return ProviderSelectionResult(
            provider_type=provider_instance.type,
            provider_name=provider_name,
            selection_reason="Explicitly specified in template",
            confidence=1.0,
        )

    def _select_by_provider_type(self, template: Any, logger: Optional[Any]) -> Any:
        """Select provider instance using load balancing within provider type."""
        try:
            from domain.base.results import ProviderSelectionResult
        except ImportError:
            pass

        provider_type = template.provider_type
        instances = self._get_enabled_instances_by_type(provider_type)
        if not instances:
            raise ValueError(f"No enabled instances found for provider type '{provider_type}'")

        selected_instance = self._apply_load_balancing_strategy(instances)

        if logger:
            logger.info(
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

    def _select_by_api_capability(self, template: Any, logger: Optional[Any]) -> Any:
        """Select provider based on API capability support."""
        try:
            from domain.base.results import ProviderSelectionResult
        except ImportError:
            pass

        provider_api = template.provider_api
        compatible_instances = self._find_compatible_providers(provider_api)
        if not compatible_instances:
            raise ValueError(f"No providers support API '{provider_api}'")

        selected_instance = self._select_best_compatible_instance(compatible_instances)

        if logger:
            logger.info(
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

    def _select_default_provider(self, template: Any, logger: Optional[Any]) -> Any:
        """Select default provider from configuration."""
        try:
            from domain.base.results import ProviderSelectionResult
        except ImportError:
            pass

        provider_config = self._get_provider_config()

        default_provider_type = getattr(provider_config, "default_provider_type", None)
        default_provider_instance = getattr(provider_config, "default_provider_instance", None)

        if not default_provider_instance:
            enabled_instances = [p for p in provider_config.providers if p.enabled]
            if not enabled_instances:
                raise ValueError("No enabled providers found in configuration")

            default_instance = enabled_instances[0]
            default_provider_type = default_instance.type
            default_provider_instance = default_instance.name

        if logger:
            logger.info("Selected default provider: %s", default_provider_instance)

        return ProviderSelectionResult(
            provider_type=default_provider_type,
            provider_name=default_provider_instance,
            selection_reason="Configuration default (no provider specified in template)",
            confidence=0.7,
        )

    def _get_cli_override(self) -> Optional[str]:
        """Get CLI provider override from configuration."""
        return None  # Simplified - CLI override handled at higher level

    def _get_provider_config(self) -> Optional[Any]:
        """Get provider configuration via lazy injection."""
        try:
            config_port = self._get_config_port()
            if config_port:
                return config_port.get_provider_config()
        except Exception as e:
            if self._logger:
                self._logger.debug("Failed to get provider configuration: %s", e)
        return None

    def _get_config_port(self) -> Optional[Any]:
        """Get configuration port from DI container using lazy injection."""
        try:
            from infrastructure.di.container import get_container
            from domain.base.ports.configuration_port import ConfigurationPort

            container = get_container()
            return container.get(ConfigurationPort)
        except Exception as e:
            if self._logger:
                self._logger.debug("Failed to get configuration port: %s", e)
            return None

    def _get_provider_instance_config(self, provider_name: str) -> Optional[Any]:
        """Get provider instance configuration by name."""
        provider_config = self._get_provider_config()
        if not provider_config:
            return None
        for provider in provider_config.providers:
            if provider.name == provider_name:
                return provider
        return None

    def _get_enabled_instances_by_type(self, provider_type: str) -> list[Any]:
        """Get all enabled provider instances of specified type."""
        provider_config = self._get_provider_config()
        if not provider_config:
            return []
        return [
            provider
            for provider in provider_config.providers
            if provider.type == provider_type and provider.enabled
        ]

    def _apply_load_balancing_strategy(
        self, instances: list[Any], selection_policy: str = None
    ) -> Any:
        """Apply load balancing strategy to select instance."""
        provider_config = self._get_provider_config()
        if not selection_policy and provider_config:
            selection_policy = provider_config.selection_policy

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
        provider_config = self._get_provider_config()
        if not provider_config:
            return []

        compatible = []
        for provider in provider_config.providers:
            if not provider.enabled:
                continue
            if self._provider_supports_api(provider, provider_api):
                compatible.append(provider)
        return compatible

    def _provider_supports_api(self, provider: Any, api: str) -> bool:
        """Check if provider instance supports the specified API."""
        provider_config = self._get_provider_config()
        provider_defaults = provider_config.provider_defaults.get(provider.type)
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
                # Use basic logger - no DI container dependency
                _provider_registry_instance = ProviderRegistry()

    return _provider_registry_instance
