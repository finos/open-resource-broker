"""Lazy loading configuration for the DI container."""

from typing import Any, Optional


class LazyLoadingConfig:
    """Configuration for lazy loading behavior."""

    def __init__(self, config_dict: Optional[dict[str, Any]] = None) -> None:
        """Initialize lazy loading configuration with provided settings."""
        if config_dict is None:
            config_dict = {}

        self.enabled = config_dict.get("enabled", True)
        self.cache_instances = config_dict.get("cache_instances", True)
        self.discovery_mode = config_dict.get("discovery_mode", "lazy")
        self.connection_mode = config_dict.get("connection_mode", "lazy")
        self.preload_critical = config_dict.get("preload_critical", [])

    @classmethod
    def from_config_manager(cls, container: Any = None) -> "LazyLoadingConfig":
        """Create lazy loading config from configuration manager."""
        if container is None:
            # Fallback during bootstrap - use safe defaults
            return cls()

        try:
            from config.managers.configuration_manager import ConfigurationManager

            config_manager = container.get(ConfigurationManager)
            performance_config = config_manager.get("performance", {})
            lazy_config = performance_config.get("lazy_loading", {})
            return cls(lazy_config)
        except Exception:
            return cls()
