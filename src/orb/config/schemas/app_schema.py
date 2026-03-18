"""Main application configuration schema."""

import os
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .common_schema import (
    EventsConfig,
    NamingConfig,
    RequestConfig,
    ResourceConfig,
)
from .logging_schema import LoggingConfig
from .metrics_schema import MetricsConfig
from .native_spec_schema import NativeSpecConfig
from .performance_schema import CircuitBreakerConfig, PerformanceConfig
from .provider_strategy_schema import ProviderConfig
from .scheduler_schema import SchedulerConfig
from .server_schema import ServerConfig
from .storage_schema import StorageConfig
from .template_schema import TemplateConfig


class AppConfig(BaseModel):
    """Application configuration."""

    version: str = Field("2.0.0", description="Configuration version")
    provider: ProviderConfig
    scheduler: SchedulerConfig = Field(default_factory=lambda: SchedulerConfig())  # type: ignore[call-arg]
    naming: NamingConfig = Field(default_factory=lambda: NamingConfig())  # type: ignore[call-arg]
    logging: LoggingConfig = Field(default_factory=lambda: LoggingConfig())  # type: ignore[call-arg]
    metrics: MetricsConfig = Field(default_factory=lambda: MetricsConfig())  # type: ignore[call-arg]
    template: Optional[TemplateConfig] = None
    events: EventsConfig = Field(default_factory=lambda: EventsConfig())  # type: ignore[call-arg]
    storage: StorageConfig = Field(default_factory=lambda: StorageConfig())  # type: ignore[call-arg]
    resource: ResourceConfig = Field(default_factory=lambda: ResourceConfig())  # type: ignore[call-arg]
    request: RequestConfig = Field(default_factory=lambda: RequestConfig())  # type: ignore[call-arg]
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=lambda: CircuitBreakerConfig())  # type: ignore[call-arg]
    performance: PerformanceConfig = Field(default_factory=lambda: PerformanceConfig())  # type: ignore[call-arg]
    server: ServerConfig = Field(default_factory=lambda: ServerConfig())  # type: ignore[call-arg]
    native_spec: NativeSpecConfig = Field(default_factory=lambda: NativeSpecConfig())  # type: ignore[call-arg]
    environment: str = Field("development", description="Environment")
    debug: bool = Field(False, description="Debug mode")

    @model_validator(mode="after")
    def ensure_template_config(self) -> "AppConfig":
        """Ensure template configuration is present."""
        if self.template is None:
            object.__setattr__(
                self,
                "template",
                TemplateConfig(),  # type: ignore[call-arg]
            )
        return self

    def get_config_file_path(self) -> str:
        """Build full config file path using scheduler + provider type."""
        config_root = self.scheduler.get_config_root()
        # Get provider type directly from config without DI container
        provider_type = self._get_selected_provider_type()
        # Generate provider-specific config file name
        config_file = f"{provider_type}prov_config.json"
        return os.path.join(config_root, config_file)

    def _get_selected_provider_type(self) -> str:
        """Get provider type directly from config without circular dependency."""
        # Get first active provider directly from config
        active_providers = self.provider.get_active_providers()
        if active_providers:
            return active_providers[0].type
        return "aws"  # Ultimate fallback

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        """
        Validate environment.

        Args:
            v: Value to validate

        Returns:
            Validated value

        Raises:
            ValueError: If environment is invalid
        """
        valid_environments = ["development", "testing", "staging", "production"]
        if v not in valid_environments:
            raise ValueError(f"Environment must be one of {valid_environments}")
        return v


def validate_config(config: dict[str, Any]) -> AppConfig:
    """
    Validate configuration.

    Args:
        config: Configuration to validate

    Returns:
        Validated configuration

    Raises:
        ValueError: If configuration is invalid
    """
    # Rebuild model to ensure all forward references are resolved
    AppConfig.model_rebuild()
    return AppConfig(**config)
