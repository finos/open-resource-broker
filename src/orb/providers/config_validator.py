"""Provider Configuration Validator - Validates provider configurations.

This module extracts validation logic from the Provider Strategy Factory,
following SRP and making validation logic reusable and testable.
"""

from typing import Any

from orb.config.schemas.provider_strategy_schema import ProviderMode
from orb.domain.base.ports import ConfigurationPort, LoggingPort
from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort
from orb.providers.config_builder import ProviderConfigBuilder


class ProviderConfigValidator:
    """Validates provider configurations."""

    def __init__(
        self,
        config_manager: ConfigurationPort,
        config_builder: ProviderConfigBuilder,
        logger: LoggingPort,
        registry: ProviderRegistryPort,
    ) -> None:
        """Initialize validator.

        Args:
            config_manager: Configuration manager instance
            config_builder: Configuration builder instance
            logger: Logger instance
            registry: Provider registry port
        """
        self._config_manager = config_manager
        self._config_builder = config_builder
        self._logger = logger
        self._registry = registry

    def validate_configuration(self) -> dict[str, Any]:
        """Validate current provider configuration.

        Returns:
            Validation result dictionary with:
            - valid: bool - Overall validation status
            - errors: list[str] - Validation errors
            - warnings: list[str] - Validation warnings
            - provider_count: int - Number of active providers
            - mode: str - Provider mode
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
            self._validate_mode(mode, active_providers, validation_result)

            # Validate provider configurations
            self._validate_providers(active_providers, validation_result)

            # Set overall validation status
            validation_result["valid"] = len(validation_result["errors"]) == 0

        except Exception as e:
            validation_result["errors"].append(f"Configuration validation failed: {e!s}")

        return validation_result

    def _validate_mode(
        self,
        mode: ProviderMode,
        active_providers: list,
        validation_result: dict[str, Any],
    ) -> None:
        """Validate provider mode configuration.

        Args:
            mode: Provider mode
            active_providers: List of active provider instances
            validation_result: Validation result dictionary to update
        """
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
        elif mode == ProviderMode.MULTI:
            if len(active_providers) < 2:
                validation_result["errors"].append(
                    "Multi-provider mode requires at least 2 active providers"
                )

    def _validate_providers(
        self, active_providers: list, validation_result: dict[str, Any]
    ) -> None:
        """Validate individual provider configurations.

        Args:
            active_providers: List of active provider instances
            validation_result: Validation result dictionary to update
        """
        registry = self._registry

        for provider_instance in active_providers:
            try:
                # Test provider strategy creation
                config = self._config_builder.build_config(provider_instance)
                registry.get_or_create_strategy(provider_instance.name, config)
            except Exception as e:
                validation_result["errors"].append(
                    f"Provider '{provider_instance.name}' validation failed: {e!s}"
                )
