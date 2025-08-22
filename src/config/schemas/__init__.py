"""Configuration schemas package."""

from .app_schema import AppConfig, validate_config
from .common_schema import (
    DatabaseConfig,
    EventsConfig,
    LimitsConfig,
    NamingConfig,
    PrefixConfig,
    RequestConfig,
    ResourceConfig,
    ResourcePrefixConfig,
    StatusValuesConfig,
)
from .logging_schema import LoggingConfig
from .performance_schema import (
    AdaptiveBatchSizingConfig,
    BatchSizesConfig,
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
    DynamodbStrategyConfig,
    JsonStrategyConfig,
    RetryConfig,
    SqlStrategyConfig,
    StorageConfig,
)
from .template_schema import TemplateConfig

__all__: list[str] = [
    # Main configuration
    "AppConfig",
    "validate_config",
    # Provider configurations
    "ProviderConfig",
    # Provider strategy configurations
    "ProviderInstanceConfig",
    "ProviderMode",
    "HealthCheckConfig",
    "StrategyCircuitBreakerConfig",
    # Template configuration
    "TemplateConfig",
    # Storage configurations
    "StorageConfig",
    "JsonStrategyConfig",
    "SqlStrategyConfig",
    "DynamodbStrategyConfig",
    "BackoffConfig",
    "RetryConfig",
    # Logging configuration
    "LoggingConfig",
    # Performance configurations
    "PerformanceConfig",
    "CircuitBreakerConfig",
    "BatchSizesConfig",
    "AdaptiveBatchSizingConfig",
    # Common configurations
    "NamingConfig",
    "RequestConfig",
    "DatabaseConfig",
    "EventsConfig",
    "ResourceConfig",
    "ResourcePrefixConfig",
    "PrefixConfig",
    "StatusValuesConfig",
    "LimitsConfig",
    # Server configurations
    "ServerConfig",
    "AuthConfig",
    "CORSConfig",
]
