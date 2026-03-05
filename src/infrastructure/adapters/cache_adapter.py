"""Cache service adapter implementing application cache port."""

from typing import Any, Optional

from application.ports.cache_service_port import CacheServicePort


class CacheServiceAdapter(CacheServicePort):
    """Adapter for cache service operations."""

    def __init__(self, cache_service: Any) -> None:
        """Initialize with cache service.

        Args:
            cache_service: Underlying cache implementation
        """
        self._cache = cache_service

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        if hasattr(self._cache, "get"):
            return self._cache.get(key)
        return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value in cache."""
        if hasattr(self._cache, "set"):
            if ttl is not None:
                self._cache.set(key, value, ttl)
            else:
                self._cache.set(key, value)

    async def delete(self, key: str) -> None:
        """Delete value from cache."""
        if hasattr(self._cache, "delete"):
            self._cache.delete(key)

    async def clear(self) -> None:
        """Clear all cache entries."""
        if hasattr(self._cache, "clear"):
            self._cache.clear()

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        if hasattr(self._cache, "exists"):
            return self._cache.exists(key)
        return await self.get(key) is not None
