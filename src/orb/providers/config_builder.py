"""Provider Configuration Builder - Separates config creation from factory logic.

This module extracts configuration building logic from the Provider Strategy Factory,
following SRP and making the code more maintainable and testable.
"""

from typing import Any

from orb.config.schemas.provider_strategy_schema import ProviderInstanceConfig
from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort


class ProviderConfigBuilder:
    """Builds provider configurations via the provider registry."""

    def __init__(self, logger: LoggingPort, registry: ProviderRegistryPort) -> None:
        """Initialize config builder.

        Args:
            logger: Logger instance for logging config operations
            registry: Provider registry port
        """
        self._logger = logger
        self._registry = registry

    def build_config(self, instance_config: ProviderInstanceConfig) -> Any:
        """Build provider configuration via the provider registry.

        Args:
            instance_config: Provider instance configuration

        Returns:
            Provider-specific configuration object

        Raises:
            RuntimeError: If the registry is not populated or has no config factory
                for the requested provider type. This indicates a bootstrap ordering
                bug and must surface loudly rather than silently falling through.
        """
        registry = self._registry
        if not registry.is_provider_registered(instance_config.type):
            raise RuntimeError(
                f"Provider type '{instance_config.type}' is not registered. "
                "This indicates a bootstrap ordering bug: register_all_provider_types() "
                "must run before ProviderConfigBuilder.build_config() is called."
            )

        config_factory = registry.get_config_factory(instance_config.type)
        if config_factory is None:
            raise RuntimeError(
                f"No config factory registered for provider type '{instance_config.type}'. "
                "Each provider must register a config factory via the provider registry."
            )

        return config_factory(instance_config)
