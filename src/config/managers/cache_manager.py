"""Configuration caching and reloading management."""

import logging
import threading
from typing import Any, Dict, Optional, Type, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


class ConfigCacheManager:
    """Manages configuration caching and reloading."""

    def __init__(self):
        """Initialize the instance."""
        self._config_cache: Dict[Type, Any] = {}
        self._cache_lock = threading.RLock()
        self._last_reload_time: Optional[float] = None

    def get_cached_config(self, config_type: Type[T]) -> Optional[T]:
        """Get cached configuration object."""
        with self._cache_lock:
            return self._config_cache.get(config_type)

    def cache_config(self, config_type: Type[T], config_instance: T) -> None:
        """Cache configuration object."""
        with self._cache_lock:
            self._config_cache[config_type] = config_instance
            logger.debug("Cached configuration for %s", config_type.__name__)

    def clear_cache(self) -> None:
        """Clear all cached configurations."""
        with self._cache_lock:
            self._config_cache.clear()
            logger.info("Configuration cache cleared")

    def clear_config_cache(self, config_type: Type[T]) -> None:
        """Clear cache for specific configuration type."""
        with self._cache_lock:
            if config_type in self._config_cache:
                del self._config_cache[config_type]
                logger.debug("Cleared cache for %s", config_type.__name__)

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._cache_lock:
            return {
                "cached_types": [cls.__name__ for cls in self._config_cache.keys()],
                "cache_size": len(self._config_cache),
                "last_reload_time": self._last_reload_time,
            }

    def mark_reload(self, reload_time: float) -> None:
        """Mark when configuration was last reloaded."""
        with self._cache_lock:
            self._last_reload_time = reload_time
            logger.info("Configuration reload marked at %s", reload_time)

    def is_cache_valid(self, max_age_seconds: Optional[float] = None) -> bool:
        """Check if cache is still valid based on age."""
        if max_age_seconds is None:
            return True

        if self._last_reload_time is None:
            return False

        import time

        current_time = time.time()
        age = current_time - self._last_reload_time

        return age <= max_age_seconds
