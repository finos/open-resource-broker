"""Infrastructure AMI cache service."""

import time
from typing import Dict, Any, Optional


class AMICacheService:
    """Infrastructure service for AMI resolution caching with TTL support."""

    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 1000):
        """Initialize cache with TTL and size limits."""
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._failed: Dict[str, float] = {}  # Failed entries with timestamp
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._stats = {"hits": 0, "misses": 0, "evictions": 0, "total_requests": 0}

    def get(self, key: str) -> Optional[str]:
        """Get cached AMI ID if not expired."""
        self._stats["total_requests"] += 1

        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry["timestamp"] < self._ttl_seconds:
                self._stats["hits"] += 1
                return entry["data"]

        self._stats["misses"] += 1
        return None

    def set(self, key: str, data: str) -> None:
        """Cache AMI ID with timestamp."""
        if len(self._cache) >= self._max_entries:
            # Remove oldest entry
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k]["timestamp"])
            del self._cache[oldest_key]
            self._stats["evictions"] += 1

        self._cache[key] = {"data": data, "timestamp": time.time()}

    def mark_failed(self, key: str) -> None:
        """Mark key as failed to avoid retry storms."""
        self._failed[key] = time.time()

    def is_failed(self, key: str) -> bool:
        """Check if key recently failed (within TTL)."""
        if key in self._failed:
            if time.time() - self._failed[key] < self._ttl_seconds:
                return True
            else:
                del self._failed[key]  # Expired failure
        return False

    def get_stale(self, key: str) -> Optional[str]:
        """Get cache entry even if expired (for fallback)."""
        if key in self._cache:
            return self._cache[key]["data"]
        return None

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            **self._stats,
            "cache_size": len(self._cache),
            "failed_size": len(self._failed),
            "hit_rate": self._stats["hits"] / max(self._stats["total_requests"], 1),
        }

    def clear(self) -> None:
        """Clear all cache data."""
        self._cache.clear()
        self._failed.clear()
        self._stats = {key: 0 for key in self._stats}

    def clear_expired(self) -> int:
        """Remove expired entries and return count."""
        current_time = time.time()
        expired_keys = [
            key
            for key, entry in self._cache.items()
            if current_time - entry["timestamp"] >= self._ttl_seconds
        ]
        for key in expired_keys:
            del self._cache[key]

        expired_failed = [
            key
            for key, timestamp in self._failed.items()
            if current_time - timestamp >= self._ttl_seconds
        ]
        for key in expired_failed:
            del self._failed[key]

        return len(expired_keys) + len(expired_failed)