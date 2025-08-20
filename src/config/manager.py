"""
Centralized configuration management - Refactored.

This module now imports from the organized configuration managers package.
All functionality maintains backward compatibility.
"""

from typing import Optional

# Import supporting classes for direct access if needed
from .managers import (
    ConfigCacheManager,
    ConfigPathResolver,
    ConfigTypeConverter,
    ProviderConfigManager,
)

# Import the main configuration manager from the new modular structure
from .managers.configuration_manager import ConfigurationManager

# Singleton instance management
_config_manager_instance = None
_config_manager_lock = None


def get_config_manager(config_path: Optional[str] = None) -> ConfigurationManager:
    """
    Get singleton configuration manager instance.

    Args:
        config_path: Optional path to configuration file

    Returns:
        ConfigurationManager instance
    """
    global _config_manager_instance, _config_manager_lock

    if _config_manager_lock is None:
        import threading

        _config_manager_lock = threading.Lock()

    with _config_manager_lock:
        if _config_manager_instance is None:
            _config_manager_instance = ConfigurationManager(config_path)
        return _config_manager_instance


# Backward compatibility - re-export main class
__all__: list[str] = [
    "ConfigurationManager",
    "ConfigTypeConverter",
    "ConfigPathResolver",
    "ProviderConfigManager",
    "ConfigCacheManager",
    "get_config_manager",
]
