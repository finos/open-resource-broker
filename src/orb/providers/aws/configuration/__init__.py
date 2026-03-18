"""AWS provider configuration."""

from .batch_sizes_config import AWSBatchSizesConfig
from .cleanup_config import CleanupConfig, CleanupResourcesConfig
from .config import (
    AWSProviderConfig,
    HandlerCapabilityConfig,
    HandlerDefaultsConfig,
    HandlersConfig,
)
from .naming_config import AWSNamingConfig
from .validator import AWSConfigManager, get_aws_config_manager

__all__: list[str] = [
    "AWSBatchSizesConfig",
    "AWSConfigManager",
    "AWSNamingConfig",
    "AWSProviderConfig",
    "CleanupConfig",
    "CleanupResourcesConfig",
    "HandlerCapabilityConfig",
    "HandlerDefaultsConfig",
    "HandlersConfig",
    "get_aws_config_manager",
]
