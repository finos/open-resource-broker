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
        # Use the logger from BaseRegistry
        self._logger = self.logger

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

    def select_provider_for_template(self, template: Any) -> Any:
        """
        Select provider instance for template requirements.

        Moved from ProviderSelectionService to maintain Clean Architecture.
        Registry is the appropriate place for provider selection logic.
        """
        from providers.results import ProviderSelectionResult

        # Get template requirements
        required_provider_type = getattr(template, "provider_type", None) or "aws"
        required_capabilities = getattr(template, "capabilities", None) or []

        # Find matching provider instances
        matching_providers = []
        for instance_name in self.get_registered_provider_instances():
            registration = self.get_provider_instance_registration(instance_name)
            if registration and registration.type_name == required_provider_type:
                strategy = self.get_or_create_strategy(instance_name)
                if strategy and self._provider_supports_capabilities(
                    strategy, required_capabilities
                ):
                    matching_providers.append((instance_name, strategy))

        if not matching_providers:
            # Try provider types if no instances found
            for provider_type in self.get_registered_providers():
                if provider_type == required_provider_type:
                    strategy = self.get_or_create_strategy(provider_type)
                    if strategy and self._provider_supports_capabilities(
                        strategy, required_capabilities
                    ):
                        matching_providers.append((provider_type, strategy))

        if not matching_providers:
            raise ValueError(f"No provider instances found for type '{required_provider_type}'")

        # Select best provider (load balancing, health, etc.)
        selected_instance, selected_strategy = self._select_best_provider(matching_providers)

        return ProviderSelectionResult(
            provider_type=required_provider_type,
            provider_name=selected_instance,
            selection_reason=f"Selected {selected_instance} for {required_provider_type}",
            confidence=1.0,
            alternatives=[name for name, _ in matching_providers if name != selected_instance],
        )

    def select_active_provider(self) -> Any:
        """
        Select active provider instance from configuration.

        Returns the first healthy provider instance, or configured default.
        """
        from providers.results import ProviderSelectionResult

        if not self._registrations:
            raise ValueError("No provider instances registered")

        # Check for configured default provider
        default_provider = self._get_default_provider_from_config()
        if default_provider and default_provider in self._registrations:
            strategy = self.get_or_create_strategy(default_provider)
            if strategy:
                return ProviderSelectionResult(
                    provider_type=getattr(strategy, "provider_type", "aws"),
                    provider_name=default_provider,
                    selection_reason=f"Using configured default provider: {default_provider}",
                    confidence=1.0,
                )

        # Select first healthy provider
        for instance_name in self.get_registered_provider_instances():
            strategy = self.get_or_create_strategy(instance_name)
            if strategy and self._is_provider_healthy(strategy):
                return ProviderSelectionResult(
                    provider_type=getattr(strategy, "provider_type", "aws"),
                    provider_name=instance_name,
                    selection_reason=f"Selected healthy provider: {instance_name}",
                    confidence=1.0,
                )

        # Fallback to first available provider
        available_instances = self.get_registered_provider_instances()
        if available_instances:
            first_instance = available_instances[0]
            strategy = self.get_or_create_strategy(first_instance)
            return ProviderSelectionResult(
                provider_type=getattr(strategy, "provider_type", "aws"),
                provider_name=first_instance,
                selection_reason=f"Fallback to first available provider: {first_instance}",
                confidence=0.7,
            )

        # Try provider types as final fallback
        available_types = self.get_registered_providers()
        if available_types:
            first_type = available_types[0]
            strategy = self.get_or_create_strategy(first_type)
            return ProviderSelectionResult(
                provider_type=first_type,
                provider_name=first_type,
                selection_reason=f"Fallback to first available provider type: {first_type}",
                confidence=0.5,
            )

        raise ValueError("No providers available")

    def _provider_supports_capabilities(self, strategy: Any, capabilities: List[str]) -> bool:
        """Check if provider strategy supports required capabilities."""
        if not capabilities:
            return True

        provider_capabilities = getattr(strategy, "supported_capabilities", [])
        return all(cap in provider_capabilities for cap in capabilities)

    def _select_best_provider(self, providers: List[tuple[str, Any]]) -> tuple[str, Any]:
        """Select best provider from available options."""
        # Simple selection: first healthy provider
        for instance_name, strategy in providers:
            if self._is_provider_healthy(strategy):
                return instance_name, strategy

        # Fallback to first provider
        return providers[0]

    def _is_provider_healthy(self, strategy: Any) -> bool:
        """Check if provider strategy is healthy."""
        # Basic health check - can be enhanced
        try:
            if hasattr(strategy, "is_healthy"):
                return strategy.is_healthy()
            if hasattr(strategy, "check_health"):
                health = strategy.check_health()
                return health is not None and health.get("healthy", True)
            return True  # Assume healthy if no health check available
        except Exception:
            return False

    def _get_default_provider_from_config(self) -> Optional[str]:
        """Get default provider from configuration."""
        try:
            from infrastructure.di.container import get_container
            from domain.base.ports.configuration_port import ConfigurationPort

            container = get_container()
            config_port = container.get(ConfigurationPort)
            provider_config = config_port.get_provider_config()

            if provider_config:
                # Check for default provider instance
                default_instance = getattr(provider_config, "default_provider_instance", None)
                if default_instance:
                    return default_instance

                # Check for first active provider
                active_providers = provider_config.get_active_providers()
                if active_providers:
                    return active_providers[0].name
        except Exception:
            pass  # Ignore configuration errors

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
                # Use basic logger - no DI container dependency
                _provider_registry_instance = ProviderRegistry()

    return _provider_registry_instance
