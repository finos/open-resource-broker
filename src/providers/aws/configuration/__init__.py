"""AWS provider configuration."""

from .config import (
    AWSProviderConfig,
    HandlerCapabilityConfig,
    HandlerDefaultsConfig,
    HandlersConfig,
)
from .validator import AWSConfigManager, AWSNamingConfig, get_aws_config_manager

__all__: list[str] = [
    "AWSProviderConfig",
    "HandlersConfig",
    "HandlerCapabilityConfig",
    "HandlerDefaultsConfig",
    "AWSNamingConfig",
    "AWSConfigManager",
    "get_aws_config_manager",
]
