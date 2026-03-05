"""Entity cache management components."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from infrastructure.logging.logger import get_logger


class EntityCache(ABC):
    """Base interface for entity caching."""

    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        """Get cached entity by key."""

    @abstractmethod
    def put(self, key: str, entity: Any) -> None:
        """Cache entity with key."""

    @abstractmethod
    def remove(self, key: str) -> None:
        """Remove entity from cache."""

    @abstractmethod
    def clear(self) -> None:
        """Clear all cached entities."""


class MemoryEntityCache(EntityCache):
    """In-memory entity cache implementation."""

    def __init__(self) -> None:
        """Initialize cache."""
        self._cache: dict[str, Any] = {}
        self.logger = get_logger(__name__)

    def get(self, key: str) -> Optional[Any]:
        """Get cached entity by key."""
        return self._cache.get(key)

    def put(self, key: str, entity: Any) -> None:
        """Cache entity with key."""
        self._cache[key] = entity

    def remove(self, key: str) -> None:
        """Remove entity from cache."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cached entities."""
        self._cache.clear()


class NoOpEntityCache(EntityCache):
    """No-operation cache that doesn't cache anything."""

    def get(self, key: str) -> Optional[Any]:
        """Always return None (no caching)."""
        return None

    def put(self, key: str, entity: Any) -> None:
        """Do nothing (no caching)."""
        pass

    def remove(self, key: str) -> None:
        """Do nothing (no caching)."""
        pass

    def clear(self) -> None:
        """Do nothing (no caching)."""
        pass
