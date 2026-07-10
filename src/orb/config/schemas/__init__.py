"""Configuration schemas package."""

from .app_schema import AppConfig, validate_config
from .common_schema import (
    DatabaseConfig,
    EventsConfig,
    NamingConfig,
    PrefixConfig,
    RequestConfig,
    ResourceConfig,
    ResourcePrefixConfig,
    StatusValuesConfig,
)
from .logging_schema import LoggingConfig
from .observability_schema import OtelConfig
from .performance_schema import (
    AdaptiveBatchSizingConfig,
    CircuitBreakerConfig,
    PerformanceConfig,
)
from .provider_strategy_schema import (
    CircuitBreakerConfig as StrategyCircuitBreakerConfig,
    HealthCheckConfig,
    ProviderConfig,
    ProviderInstanceConfig,
    ProviderMode,
)
from .server_schema import AuthConfig, CORSConfig, ServerConfig
from .storage_schema import (
    BackoffConfig,
    JsonStrategyConfig,
    RetryConfig,
    SqlStrategyConfig,
    StorageConfig,
)
from .template_schema import TemplateConfig
from .ui_schema import UIConfig

__all__: list[str] = [
    "AdaptiveBatchSizingConfig",
    # Main configuration
    "AppConfig",
    "AuthConfig",
    "BackoffConfig",
    "CORSConfig",
    "CircuitBreakerConfig",
    "DatabaseConfig",
    "EventsConfig",
    "HealthCheckConfig",
    "JsonStrategyConfig",
    # Logging configuration
    "LoggingConfig",
    # Common configurations
    "NamingConfig",
    # Performance configurations
    "PerformanceConfig",
    "PrefixConfig",
    # Provider configurations
    "ProviderConfig",
    # Provider strategy configurations
    "ProviderInstanceConfig",
    "ProviderMode",
    "RequestConfig",
    "ResourceConfig",
    "ResourcePrefixConfig",
    "RetryConfig",
    # Server configurations
    "ServerConfig",
    "SqlStrategyConfig",
    "StatusValuesConfig",
    # Storage configurations
    "StorageConfig",
    "StrategyCircuitBreakerConfig",
    # Observability configuration
    "OtelConfig",
    # Template configuration
    "TemplateConfig",
    # UI configuration
    "UIConfig",
    "validate_config",
]
