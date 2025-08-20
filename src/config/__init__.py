"""Configuration package with clean public API."""

# Main configuration classes
from .loader import ConfigurationLoader

# Configuration management
from .manager import ConfigurationManager
from .schemas import (
    AppConfig,
    BackoffConfig,
    CircuitBreakerConfig,
    DatabaseConfig,
    EventsConfig,
    LimitsConfig,
    LoggingConfig,
    NamingConfig,
    PerformanceConfig,
    ProviderConfig,
    RequestConfig,
    ResourceConfig,
    SqlStrategyConfig,
    StatusValuesConfig,
    StorageConfig,
    TemplateConfig,
    validate_config,
)

# Validation
from .validators import ConfigValidator

__all__: list[str] = [
    # Main configuration
    "AppConfig",
    "validate_config",
    # Provider configurations
    "ProviderConfig",
    # Specific configurations
    "TemplateConfig",
    "StorageConfig",
    "LoggingConfig",
    "PerformanceConfig",
    "NamingConfig",
    "RequestConfig",
    "DatabaseConfig",
    "EventsConfig",
    "StatusValuesConfig",
    "BackoffConfig",
    "LimitsConfig",
    "CircuitBreakerConfig",
    "SqlStrategyConfig",
    "ResourceConfig",
    # Validation
    "ConfigValidator",
    # Configuration management
    "ConfigurationManager",
    "ConfigurationLoader",
]
