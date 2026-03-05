"""
Centralized configuration management - Refactored.

This module now imports from the organized configuration managers package.
All functionality maintains backward compatibility.
"""

# Import supporting classes for direct access if needed
from .managers import (
    ConfigCacheManager,
    ConfigPathResolver,
    ConfigTypeConverter,
    ProviderConfigManager,
)

# Import the main configuration manager from the new modular structure
from .managers.configuration_manager import ConfigurationManager

# Backward compatibility - re-export main class
__all__: list[str] = [
    "ConfigCacheManager",
    "ConfigPathResolver",
    "ConfigTypeConverter",
    "ConfigurationManager",
    "ProviderConfigManager",
]
