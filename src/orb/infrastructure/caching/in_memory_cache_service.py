"""In-memory implementation of CacheServicePort."""

import time
from typing import Any, Dict, Optional, Tuple

from orb.application.ports.cache_service_port import CacheServicePort


class InMemoryCacheService(CacheServicePort):
    """Simple in-memory cache implementing CacheServicePort.

    Uses a dict with optional TTL. Not thread-safe for high-concurrency use,
    but sufficient for single-process request caching.
    """

    def __init__(self, default_ttl: Optional[int] = 300) -> None:
        self._store: Dict[str, Tuple[Any, Optional[float]]] = {}  # key -> (value, expiry)
        self._default_ttl = default_ttl

    async def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if expiry is not None and time.monotonic() > expiry:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        effective_ttl = ttl if ttl is not None else self._default_ttl
        expiry = time.monotonic() + effective_ttl if effective_ttl is not None else None
        self._store[key] = (value, expiry)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def clear(self) -> None:
        self._store.clear()

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    def is_caching_enabled(self) -> bool:
        return True

    def get_cached_request(self, request_id: str) -> Any:
        """Synchronous cache lookup for request DTOs."""
        entry = self._store.get(f"request:{request_id}")
        if entry is None:
            return None
        value, expiry = entry
        if expiry is not None and time.monotonic() > expiry:
            del self._store[f"request:{request_id}"]
            return None
        return value

    def cache_request(self, request_id: str, request_dto: Any) -> None:
        """Synchronous cache write for request DTOs."""
        expiry = time.monotonic() + self._default_ttl if self._default_ttl is not None else None
        self._store[f"request:{request_id}"] = (request_dto, expiry)
