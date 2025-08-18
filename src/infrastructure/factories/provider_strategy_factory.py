"""Provider Strategy Factory - Configuration-driven provider strategy creation.

This factory creates provider strategies and contexts based on integrated configuration,
integrating the existing provider strategy ecosystem with the CQRS architecture.
"""

from typing import Any, Dict, Optional

from config.schemas.provider_strategy_schema import (
    ProviderConfig,
    ProviderInstanceConfig,
    ProviderMode,
)
from domain.base.exceptions import ConfigurationError
from domain.base.ports import ConfigurationPort, LoggingPort
from infrastructure.error.decorators import handle_infrastructure_exceptions
from infrastructure.registry.provider_registry import (
    UnsupportedProviderError,
    get_provider_registry,
)
from providers.base.strategy import (
    ProviderContext,
    ProviderStrategy,
    SelectionPolicy,
    create_provider_context,
)


class ProviderCreationError(Exception):
    """Exception raised when provider creation fails."""


class ProviderStrategyFactory:
    """Factory for creating provider strategies from integrated configuration."""

    def __init__(self, config_manager: ConfigurationPort, logger: Optional[LoggingPort] = None):
        """
        Initialize provider strategy factory.

        Args:
            config_manager: Configuration manager instance
            logger: Optional logger instance
        """
        self._config_manager = config_manager
        self._logger = logger
        self._provider_cache: Dict[str, ProviderStrategy] = {}

    @handle_infrastructure_exceptions(context="provider_context_creation")
    def create_provider_context(self) -> ProviderContext:
        """
        Create configured provider context based on integrated configuration.

        Returns:
            Configured ProviderContext instance

        Raises:
            ConfigurationError: If configuration is invalid
            ProviderCreationError: If provider creation fails
        """
        try:
            # Get integrated provider configuration
            provider_config = self._config_manager.get_provider_config()
            if not provider_config:
                raise ConfigurationError("Provider configuration not found")

            mode = provider_config.get_mode()

            self._logger.info("Creating provider context in %s mode", mode.value)

            if mode == ProviderMode.SINGLE:
                return self._create_single_provider_context(provider_config)
            elif mode == ProviderMode.MULTI:
                return self._create_multi_provider_context(provider_config)
            else:
                raise ConfigurationError("Provider", "No valid provider configuration found")

        except Exception as e:
            self._logger.error("Failed to create provider context: %s", str(e))
            raise ProviderCreationError(f"Provider context creation failed: {str(e)}") from e

    def _create_single_provider_context(self, config: ProviderConfig) -> ProviderContext:
        """
        Create single provider context.

        Args:
            config: Integrated provider configuration

        Returns:
            Configured ProviderContext for single provider
        """
        active_providers = config.get_active_providers()

        if not active_providers:
            raise ConfigurationError(
                "Provider", "No active providers found for single provider mode"
            )

        provider_config = active_providers[0]
        self._logger.info("Creating single provider context with provider: %s", provider_config.name)

        # Create provider context
        context = create_provider_context(self._logger)

        # Create and register the single provider strategy
        strategy = self._create_provider_strategy(provider_config)
        context.register_strategy(strategy, provider_config.name)

        # Configure context for single provider mode
        context.set_default_selection_policy(SelectionPolicy.FIRST_AVAILABLE)
        self._configure_context_settings(context, config)

        self._logger.info(
            "Single provider context created successfully with provider: %s",  provider_config.name
        )
        return context

    def _create_multi_provider_context(self, config: ProviderConfig) -> ProviderContext:
        """
        Create multi-provider context.

        Args:
            config: Integrated provider configuration

        Returns:
            Configured ProviderContext for multiple providers
        """
        active_providers = config.get_active_providers()

        if len(active_providers) < 2:
            raise ConfigurationError(
                "Provider",
                "At least 2 active providers required for multi-provider mode",
            )

        self._logger.info("Creating multi-provider context with %s providers", len(active_providers))

        # Create provider context
        context = create_provider_context(self._logger)

        # Create and register all provider strategies
        for provider_config in active_providers:
            try:
                strategy = self._create_provider_strategy(provider_config)
                context.register_strategy(strategy, provider_config.name)
                self._logger.debug("Registered provider strategy: %s", provider_config.name)
            except Exception as e:
                self._logger.error(
                    "Failed to create provider strategy %s: %s",  provider_config.name,  str(e)
                )
                # Continue with other providers, but log the error
                continue

        # Configure selection policy
        selection_policy = self._parse_selection_policy(config.selection_policy)
        context.set_default_selection_policy(selection_policy)

        # Configure context settings
        self._configure_context_settings(context, config)

        registered_strategies = context.get_available_strategies()
        self._logger.info(
            "Multi-provider context created with %s strategies", len(registered_strategies)
        )

        return context

    def _create_provider_strategy(
        self, provider_config: ProviderInstanceConfig
    ) -> ProviderStrategy:
        """
        Create individual provider strategy using registry pattern.

        Args:
            provider_config: Provider instance configuration

        Returns:
            Configured ProviderStrategy instance

        Raises:
            ProviderCreationError: If provider creation fails
        """
        # Check cache first
        cache_key = f"{provider_config.type}:{provider_config.name}"
        if cache_key in self._provider_cache:
            self._logger.debug("Using cached provider strategy: %s", cache_key)
            return self._provider_cache[cache_key]

        try:
            # Use registry pattern with named instances
            registry = get_provider_registry()

            # Try to create from named instance first (preferred for multi-instance)
            if registry.is_provider_instance_registered(provider_config.name):
                strategy = registry.create_strategy_from_instance(
                    provider_config.name, provider_config
                )
                self._logger.debug(
                    "Created provider strategy from instance: %s", provider_config.name
                )
            else:
                # Fallback to provider type (backward compatibility)
                strategy = registry.create_strategy(provider_config.type, provider_config)
                self._logger.debug("Created provider strategy from type: %s", provider_config.type)

            # Set provider name for identification
            if hasattr(strategy, "name"):
                strategy.name = provider_config.name

            # Cache the strategy
            self._provider_cache[cache_key] = strategy

            self._logger.debug(
                "Created provider strategy: %s (%s)",  provider_config.name,  provider_config.type
            )
            return strategy

        except UnsupportedProviderError as e:
            available_providers = get_provider_registry().get_registered_providers()
            raise ProviderCreationError(
                f"Unsupported provider type: {provider_config.type}. "
                f"Available providers: {', '.join(available_providers)}"
            ) from e
        except Exception as e:
            raise ProviderCreationError(
                f"Failed to create {provider_config.type} provider '{provider_config.name}': {str(e)}"
            ) from e

    def _parse_selection_policy(self, policy_name: str) -> SelectionPolicy:
        """
        Parse selection policy name to SelectionPolicy enum.

        Args:
            policy_name: Selection policy name

        Returns:
            SelectionPolicy enum value
        """
        policy_mapping = {
            "FIRST_AVAILABLE": SelectionPolicy.FIRST_AVAILABLE,
            "ROUND_ROBIN": SelectionPolicy.ROUND_ROBIN,
            "WEIGHTED_ROUND_ROBIN": SelectionPolicy.WEIGHTED_ROUND_ROBIN,
            "LEAST_CONNECTIONS": SelectionPolicy.LEAST_CONNECTIONS,
            "FASTEST_RESPONSE": SelectionPolicy.FASTEST_RESPONSE,
            "HIGHEST_SUCCESS_RATE": SelectionPolicy.HIGHEST_SUCCESS_RATE,
            "CAPABILITY_BASED": SelectionPolicy.CAPABILITY_BASED,
            "HEALTH_BASED": SelectionPolicy.HEALTH_BASED,
            "RANDOM": SelectionPolicy.RANDOM,
            "PERFORMANCE_BASED": SelectionPolicy.FASTEST_RESPONSE,  # Map to closest equivalent
            "CUSTOM": SelectionPolicy.CUSTOM,
        }

        if policy_name not in policy_mapping:
            self._logger.warning("Unknown selection policy '%s', using FIRST_AVAILABLE", policy_name)
            return SelectionPolicy.FIRST_AVAILABLE

        return policy_mapping[policy_name]

    def _configure_context_settings(self, context: ProviderContext, config: ProviderConfig) -> None:
        """
        Configure provider context with global settings.

        Args:
            context: Provider context to configure
            config: Integrated provider configuration
        """
        try:
            # Configure health check interval
            if hasattr(context, "set_health_check_interval"):
                context.set_health_check_interval(config.health_check_interval)

            # Configure circuit breaker
            if hasattr(context, "configure_circuit_breaker") and config.circuit_breaker.enabled:
                context.configure_circuit_breaker(
                    failure_threshold=config.circuit_breaker.failure_threshold,
                    recovery_timeout=config.circuit_breaker.recovery_timeout,
                    half_open_max_calls=config.circuit_breaker.half_open_max_calls,
                )

            self._logger.debug("Provider context settings configured successfully")

        except Exception as e:
            self._logger.warning("Failed to configure some context settings: %s", str(e))
            # Don't fail the entire creation for optional settings

    def get_provider_info(self) -> Dict[str, Any]:
        """
        Get information about current provider configuration.

        Returns:
            Dictionary with provider configuration information
        """
        try:
            provider_config = self._config_manager.get_provider_config()
            if not provider_config:
                return {"mode": "error", "error": "Provider configuration not found"}

            mode = provider_config.get_mode()
            active_providers = provider_config.get_active_providers()

            return {
                "mode": mode.value,
                "selection_policy": provider_config.selection_policy,
                "active_provider": provider_config.active_provider,
                "total_providers": len(provider_config.providers),
                "active_providers": len(active_providers),
                "provider_names": [p.name for p in active_providers],
                "health_check_interval": provider_config.health_check_interval,
                "circuit_breaker_enabled": provider_config.circuit_breaker.enabled,
            }

        except Exception as e:
            self._logger.error("Failed to get provider info: %s", str(e))
            return {"mode": "error", "error": str(e)}

    def validate_configuration(self) -> Dict[str, Any]:
        """
        Validate current provider configuration.

        Returns:
            Validation result dictionary
        """
        validation_result = {
            "valid": False,
            "errors": [],
            "warnings": [],
            "provider_count": 0,
            "mode": "unknown",
        }

        try:
            # Get and validate integrated configuration
            provider_config = self._config_manager.get_provider_config()
            if not provider_config:
                validation_result["errors"].append("Provider configuration not found")
                return validation_result

            mode = provider_config.get_mode()
            active_providers = provider_config.get_active_providers()

            validation_result["mode"] = mode.value
            validation_result["provider_count"] = len(active_providers)

            # Validate based on mode
            if mode == ProviderMode.NONE:
                validation_result["errors"].append("No valid provider configuration found")
            elif mode == ProviderMode.SINGLE:
                if len(active_providers) == 0:
                    validation_result["errors"].append(
                        "Single provider mode requires at least one active provider"
                    )
                elif len(active_providers) > 1:
                    validation_result["warnings"].append(
                        "Multiple active providers in single provider mode"
                    )
            elif mode == ProviderMode.MULTI:  # noqa: SIM102 (false positive - if-elif structure)
                if len(active_providers) < 2:
                    validation_result["errors"].append(
                        "Multi-provider mode requires at least 2 active providers"
                    )

            # Validate provider configurations
            for provider_config in active_providers:
                try:
                    # Test provider strategy creation
                    self._create_provider_strategy(provider_config)
                except Exception as e:
                    validation_result["errors"].append(
                        f"Provider '{provider_config.name}' validation failed: {str(e)}"
                    )

            # Set overall validation status
            validation_result["valid"] = len(validation_result["errors"]) == 0

        except Exception as e:
            validation_result["errors"].append(f"Configuration validation failed: {str(e)}")

        return validation_result

    def clear_cache(self) -> None:
        """Clear provider strategy cache."""
        self._provider_cache.clear()
        self._logger.debug("Provider strategy cache cleared")
