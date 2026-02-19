"""Entity cache management components."""

from abc import ABC, abstractmethod
from typing import Any, Optional, TypeVar

from infrastructure.logging.logger import get_logger

T = TypeVar("T")


class EntityCache(ABC):
    """Base interface for entity caching."""

    @abstractmethod
    def get(self, key: str) -> Optional[T]:
        """Get cached entity by key."""

    @abstractmethod
    def put(self, key: str, entity: T) -> None:
        """Cache entity with key."""

    @abstractmethod
    def remove(self, key: str) -> None:
        """Remove entity from cache."""

    @abstractmethod
    def clear(self) -> None:
        """Clear all cached entities."""


class MemoryEntityCache(EntityCache[T]):
    """In-memory entity cache implementation."""

    def __init__(self) -> None:
        """Initialize cache."""
        self._cache: dict[str, T] = {}
        self.logger = get_logger(__name__)

    def get(self, key: str) -> Optional[T]:
        """Get cached entity by key."""
        return self._cache.get(key)

    def put(self, key: str, entity: T) -> None:
        """Cache entity with key."""
        self._cache[key] = entity

    def remove(self, key: str) -> None:
        """Remove entity from cache."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cached entities."""
        self._cache.clear()


class NoOpEntityCache(EntityCache[T]):
    """No-operation cache that doesn't cache anything."""

    def get(self, key: str) -> Optional[T]:
        """Always return None (no caching)."""
        return None

    def put(self, key: str, entity: T) -> None:
        """Do nothing (no caching)."""
        pass

    def remove(self, key: str) -> None:
        """Do nothing (no caching)."""
        pass

    def clear(self) -> None:
        """Do nothing (no caching)."""
        pass