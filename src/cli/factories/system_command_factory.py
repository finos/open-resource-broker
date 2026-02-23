"""System command factory for creating system-related commands and queries."""

from typing import Any, Optional

from application.commands.system import (
    RefreshTemplatesCommand,
    ReloadProviderConfigCommand,
    SetConfigurationCommand,
)
from application.dto.queries import (
    GetConfigurationQuery,
    ValidateMCPQuery,
    ValidateStorageQuery,
)
from application.queries.system import (
    GetProviderConfigQuery,
    GetProviderMetricsQuery,
    GetSystemStatusQuery,
    ValidateProviderConfigQuery,
)


class SystemCommandFactory:
    """Factory for creating system-related commands and queries."""

    def create_reload_provider_config_command(
        self, config_path: Optional[str] = None, **kwargs: Any
    ) -> ReloadProviderConfigCommand:
        """Create command to reload provider configuration."""
        return ReloadProviderConfigCommand(config_path=config_path)

    def create_refresh_templates_command(self, **kwargs: Any) -> RefreshTemplatesCommand:
        """Create command to refresh templates."""
        return RefreshTemplatesCommand()

    def create_get_system_status_query(
        self,
        include_health: bool = True,
        include_metrics: bool = False,
        include_config: bool = False,
        **kwargs: Any,
    ) -> GetSystemStatusQuery:
        """Create query to get system status."""
        return GetSystemStatusQuery(
            include_health=include_health,
            include_metrics=include_metrics,
            include_config=include_config,
        )

    def create_get_provider_config_query(
        self,
        provider_name: Optional[str] = None,
        include_sensitive: bool = False,
        **kwargs: Any,
    ) -> GetProviderConfigQuery:
        """Create query to get provider configuration."""
        return GetProviderConfigQuery(
            provider_name=provider_name, include_sensitive=include_sensitive
        )

    def create_get_provider_metrics_query(
        self,
        provider_name: Optional[str] = None,
        timeframe: str = "1h",
        detailed: bool = False,
        **kwargs: Any,
    ) -> GetProviderMetricsQuery:
        """Create query to get provider metrics."""
        return GetProviderMetricsQuery(
            provider_name=provider_name, timeframe=timeframe, detailed=detailed
        )

    def create_validate_provider_config_query(
        self, detailed: bool = False, **kwargs: Any
    ) -> ValidateProviderConfigQuery:
        """Create query to validate provider configuration."""
        return ValidateProviderConfigQuery(detailed=detailed)

    def create_test_storage_query(self) -> ValidateStorageQuery:
        """Create query to test storage."""
        return ValidateStorageQuery()

    def create_mcp_validate_query(self) -> ValidateMCPQuery:
        """Create query to validate MCP."""
        return ValidateMCPQuery()

    def create_get_configuration_query(
        self,
        key: str,
        default: Optional[str] = None,
        **kwargs: Any,
    ) -> GetConfigurationQuery:
        """Create query to get configuration value."""
        return GetConfigurationQuery(key=key, default=default)

    def create_set_configuration_command(
        self,
        key: str,
        value: str,
        **kwargs: Any,
    ) -> SetConfigurationCommand:
        """Create command to set configuration value."""
        return SetConfigurationCommand(key=key, value=value)
