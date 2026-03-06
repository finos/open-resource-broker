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
    "AdaptiveBatchSizingConfig",
    # Main configuration
    "AppConfig",
    "AuthConfig",
    "BackoffConfig",
    "BatchSizesConfig",
    "CORSConfig",
    "CircuitBreakerConfig",
    "DatabaseConfig",
    "DynamodbStrategyConfig",
    "EventsConfig",
    "HealthCheckConfig",
    "JsonStrategyConfig",
    "LimitsConfig",
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
    # Template configuration
    "TemplateConfig",
    "validate_config",
]
