"""Configuration port for domain layer."""

from abc import abstractmethod
from typing import Any

from .provider_config_port import ProviderConfigPort


class ConfigurationPort(ProviderConfigPort):
    """Port for configuration operations in domain layer.

    Extends ProviderConfigPort so all existing code continues to work.
    New code that only needs provider config should depend on ProviderConfigPort.
    """

    @abstractmethod
    def get_naming_config(self) -> dict[str, Any]:
        """Get naming configuration."""

    @abstractmethod
    def get_request_config(self) -> dict[str, Any]:
        """Get request configuration."""

    @abstractmethod
    def get_template_config(self) -> dict[str, Any]:
        """Get template configuration."""

    # get_provider_config inherited from ProviderConfigPort

    @abstractmethod
    def get_storage_config(self) -> dict[str, Any]:
        """Get storage configuration."""

    @abstractmethod
    def get_cache_dir(self) -> str:
        """Get cache directory path."""

    @abstractmethod
    def get_package_info(self) -> dict[str, Any]:
        """Get package metadata information."""

    @abstractmethod
    def get_events_config(self) -> dict[str, Any]:
        """Get events configuration."""

    @abstractmethod
    def get_logging_config(self) -> dict[str, Any]:
        """Get logging configuration."""

    @abstractmethod
    def get_native_spec_config(self) -> dict[str, Any]:
        """Get native spec configuration."""

    @abstractmethod
    def get_metrics_config(self) -> dict[str, Any]:
        """Get metrics configuration."""

    @abstractmethod
    def get_active_provider_override(self) -> str | None:
        """Get current provider override from CLI."""

    @abstractmethod
    def override_provider_instance(self, provider_name: str) -> None:
        """Override the active provider instance."""

    @abstractmethod
    def override_provider_region(self, region: str) -> None:
        """Override the provider region for this session."""

    @abstractmethod
    def override_provider_profile(self, profile: str) -> None:
        """Override the provider credential profile for this session."""

    @abstractmethod
    def get_effective_region(self, default_region: str = "us-east-1") -> str:
        """Get effective provider region (override or configured)."""

    @abstractmethod
    def get_effective_profile(self, default_profile: str = "default") -> str:
        """Get effective provider credential profile (override or configured)."""

    # get_provider_instance_config inherited from ProviderConfigPort

    @abstractmethod
    def get_configuration_sources(self) -> dict[str, Any]:
        """Get configuration source information."""

    @abstractmethod
    def set_configuration_value(self, key: str, value: Any) -> None:
        """Set configuration value."""

    @abstractmethod
    def get_resource_prefix(self, resource_type: str) -> str:
        """Get resource naming prefix for the given resource type."""

    @abstractmethod
    def get_cleanup_config(self) -> dict[str, Any]:
        """Get cleanup configuration."""

    @abstractmethod
    def get_config_file_path(self) -> str:
        """Get the config file path."""

    @abstractmethod
    def get_configuration_value(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key."""
        return self.get_configuration_value(key, default)

    def get_typed(self, key: str, expected_type: type, default: Any = None) -> Any:
        """Get typed configuration value."""
        return self.get_configuration_value(key, default)

    def get_typed_with_defaults(self, key: str, expected_type: type, default: Any = None) -> Any:
        """Get typed configuration value with defaults."""
        return self.get_configuration_value(key, default)

    def resolve_file(self, path: str) -> str:
        """Resolve a file path relative to configuration."""
        return path

    def get_scheduler_strategy(self) -> str:
        """Get the configured scheduler strategy."""
        return "default"

    def get_storage_strategy(self) -> str:
        """Get the configured storage strategy."""
        return "default"

    def override_scheduler_strategy(self, strategy: str) -> None:
        """Override the scheduler strategy."""

    def restore_scheduler_strategy(self) -> None:
        """Restore the original scheduler strategy."""

    def reload(self) -> None:
        """Reload configuration from sources."""

    def validate_configuration(self) -> list[Any]:
        """Validate configuration and return list of errors."""
        return []

    def get_active_providers(self) -> list[Any]:
        """Get list of active providers."""
        return []

    @property
    def app_config(self) -> Any:
        """Get the full application configuration."""
        return None
