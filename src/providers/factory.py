"""Provider Strategy Factory - Configuration-driven provider strategy creation.

This factory creates provider strategies and contexts based on integrated configuration,
integrating the existing provider strategy ecosystem with the CQRS architecture.

Refactored to follow SRP by delegating to:
- ProviderConfigBuilder: Configuration creation with env var overrides
- ProviderConfigValidator: Configuration validation logic
"""

from typing import Any, Optional

from config.schemas.provider_strategy_schema import ProviderInstanceConfig
from domain.base.exceptions import ConfigurationError
from domain.base.ports import ConfigurationPort, LoggingPort
from infrastructure.error.decorators import handle_infrastructure_exceptions
from providers.base.strategy.provider_strategy import ProviderStrategy
from providers.config_builder import ProviderConfigBuilder
from providers.config_validator import ProviderConfigValidator
from providers.registry import (
    UnsupportedProviderError,
    get_provider_registry,
)


class ProviderCreationError(Exception):
    """Exception raised when provider creation fails."""


class ProviderStrategyFactory:
    """Factory for creating provider strategies from integrated configuration.

    This factory follows SRP by delegating responsibilities:
    - Configuration building: ProviderConfigBuilder
    - Configuration validation: ProviderConfigValidator
    - Strategy creation: This factory (single responsibility)
    """

    def __init__(
        self, config_manager: ConfigurationPort, logger: Optional[LoggingPort] = None
    ) -> None:
        """
        Initialize provider strategy factory.

        Args:
            config_manager: Configuration manager instance
            logger: Optional logger instance
        """
        self._config_manager = config_manager
        self._logger = logger
        self._provider_cache: dict[str, ProviderStrategy] = {}

        # Delegate configuration building and validation
        # Use a default logger if none provided
        effective_logger: Any = logger
        if effective_logger is None:
            from infrastructure.logging.logger import get_logger

            effective_logger = get_logger(__name__)

        self._config_builder = ProviderConfigBuilder(effective_logger)
        self._config_validator = ProviderConfigValidator(
            config_manager, self._config_builder, effective_logger
        )

    @handle_infrastructure_exceptions(context="provider_registry_setup")
    def setup_provider_registry(self) -> None:
        """
        Setup provider registry based on integrated configuration.

        Raises:
            ConfigurationError: If configuration is invalid
            ProviderCreationError: If provider setup fails
        """
        try:
            # Get integrated provider configuration
            provider_config = self._config_manager.get_provider_config()
            if not provider_config:
                raise ConfigurationError("Provider configuration not found")

            mode = provider_config.get_mode()
            if self._logger:
                self._logger.info("Setting up provider registry in %s mode", mode.value)

            # Register all active providers with registry
            active_providers = provider_config.get_active_providers()
            registry = get_provider_registry()

            for provider_instance in active_providers:
                if not registry.is_provider_instance_registered(provider_instance.name):
                    registry.ensure_provider_instance_registered_from_config(provider_instance)

            if self._logger:
                self._logger.info(
                    "Provider registry setup complete with %s providers", len(active_providers)
                )

        except Exception as e:
            if self._logger:
                self._logger.error("Failed to setup provider registry: %s", str(e))
            raise ProviderCreationError(f"Provider registry setup failed: {e!s}")

    def _create_provider_config(self, instance_config: ProviderInstanceConfig) -> Any:
        """Create provider configuration with automatic env var loading.

        Delegates to ProviderConfigBuilder for actual configuration creation.

        Args:
            instance_config: Provider instance configuration

        Returns:
            Provider-specific configuration object
        """
        return self._config_builder.build_config(instance_config)

    def _create_provider_strategy(self, provider_config: ProviderInstanceConfig) -> Any:
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
            if self._logger:
                self._logger.debug("Using cached provider strategy: %s", cache_key)
            return self._provider_cache[cache_key]

        try:
            # Use registry pattern with named instances
            registry = get_provider_registry()

            # Create provider configuration
            config = self._create_provider_config(provider_config)

            # Try to create from named instance first (preferred for multi-instance)
            if registry.is_provider_instance_registered(provider_config.name):
                strategy = registry.get_or_create_strategy(provider_config.name, config)
                if self._logger:
                    self._logger.debug(
                        "Created provider strategy from instance: %s", provider_config.name
                    )
            else:
                # Fallback to provider type (backward compatibility)
                strategy = registry.get_or_create_strategy(provider_config.type, config)
                if self._logger:
                    self._logger.debug(
                        "Created provider strategy from type: %s", provider_config.type
                    )

            # Set provider name for identification
            if strategy is not None and hasattr(strategy, "name"):
                strategy.name = provider_config.name

            # Cache the strategy
            if strategy is not None:
                self._provider_cache[cache_key] = strategy

            if self._logger:
                self._logger.debug(
                    "Created provider strategy: %s (%s)",
                    provider_config.name,
                    provider_config.type,
                )
            return strategy

        except UnsupportedProviderError:
            available_providers = get_provider_registry().get_registered_providers()
            raise ProviderCreationError(
                f"Unsupported provider type: {provider_config.type}. "
                f"Available providers: {', '.join(available_providers)}"
            )
        except Exception as e:
            raise ProviderCreationError(
                f"Failed to create {provider_config.type} provider '{provider_config.name}': {e!s}"
            )

    def get_provider_info(self) -> dict[str, Any]:
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
            if self._logger:
                self._logger.error("Failed to get provider info: %s", str(e))
            return {"mode": "error", "error": str(e)}

    def validate_configuration(self) -> dict[str, Any]:
        """Validate current provider configuration.

        Delegates to ProviderConfigValidator for validation logic.

        Returns:
            Validation result dictionary with:
            - valid: bool - Overall validation status
            - errors: list[str] - Validation errors
            - warnings: list[str] - Validation warnings
            - provider_count: int - Number of active providers
            - mode: str - Provider mode
        """
        return self._config_validator.validate_configuration()

    def clear_cache(self) -> None:
        """Clear provider strategy cache."""
        self._provider_cache.clear()
        if self._logger:
            self._logger.debug("Provider strategy cache cleared")
