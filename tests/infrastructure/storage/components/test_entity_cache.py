"""Tests for EntityCache component."""

import pytest

from orb.infrastructure.storage.components.entity_cache import (
    EntityCache,
    MemoryEntityCache,
    NoOpEntityCache,
)


class TestEntityCacheInterface:
    """Test EntityCache interface."""

    def test_cache_interface_is_abstract(self):
        """Test that EntityCache is abstract."""
        with pytest.raises(TypeError):
            EntityCache()


class TestMemoryEntityCache:
    """Test MemoryEntityCache implementation."""

    def test_initialization(self):
        """Test cache initialization."""
        cache = MemoryEntityCache()
        assert cache.logger is not None
        assert cache._cache == {}

    def test_put_and_get(self):
        """Test putting and getting entities."""
        cache = MemoryEntityCache()
        entity = {"id": "test-1", "name": "Test"}

        cache.put("test-1", entity)
        result = cache.get("test-1")

        assert result == entity

    def test_get_nonexistent(self):
        """Test getting non-existent entity."""
        cache = MemoryEntityCache()

        result = cache.get("nonexistent")

        assert result is None

    def test_remove(self):
        """Test removing entity from cache."""
        cache = MemoryEntityCache()
        entity = {"id": "test-1", "name": "Test"}

        cache.put("test-1", entity)
        cache.remove("test-1")
        result = cache.get("test-1")

        assert result is None

    def test_remove_nonexistent(self):
        """Test removing non-existent entity."""
        cache = MemoryEntityCache()

        # Should not raise exception
        cache.remove("nonexistent")

    def test_clear(self):
        """Test clearing all cached entities."""
        cache = MemoryEntityCache()

        cache.put("test-1", {"id": "test-1"})
        cache.put("test-2", {"id": "test-2"})
        cache.clear()

        assert cache.get("test-1") is None
        assert cache.get("test-2") is None

    def test_multiple_entities(self):
        """Test caching multiple entities."""
        cache = MemoryEntityCache()
        entity1 = {"id": "test-1", "name": "Test 1"}
        entity2 = {"id": "test-2", "name": "Test 2"}

        cache.put("test-1", entity1)
        cache.put("test-2", entity2)

        assert cache.get("test-1") == entity1
        assert cache.get("test-2") == entity2


class TestNoOpEntityCache:
    """Test NoOpEntityCache implementation."""

    def test_get_always_returns_none(self):
        """Test that get always returns None."""
        cache = NoOpEntityCache()

        result = cache.get("any-key")

        assert result is None

    def test_put_does_nothing(self):
        """Test that put does nothing."""
        cache = NoOpEntityCache()
        entity = {"id": "test-1", "name": "Test"}

        # Should not raise exception
        cache.put("test-1", entity)

        # Should still return None
        assert cache.get("test-1") is None

    def test_remove_does_nothing(self):
        """Test that remove does nothing."""
        cache = NoOpEntityCache()

        # Should not raise exception
        cache.remove("any-key")

    def test_clear_does_nothing(self):
        """Test that clear does nothing."""
        cache = NoOpEntityCache()

        # Should not raise exception
        cache.clear()
