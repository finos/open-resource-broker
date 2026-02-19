"""Tests for infrastructure AMI cache service."""

import time
from src.infrastructure.caching.ami_cache_service import AMICacheService


class TestAMICacheService:
    """Test AMI cache service functionality."""

    def test_cache_initialization(self):
        """Test cache initializes with correct defaults."""
        cache = AMICacheService()
        assert len(cache._cache) == 0
        assert len(cache._failed) == 0

    def test_cache_hit(self):
        """Test cache returns cached result."""
        cache = AMICacheService(ttl_seconds=60)
        cache.set("test-key", "ami-12345678")

        result = cache.get("test-key")
        assert result == "ami-12345678"

        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 0
        assert stats["total_requests"] == 1

    def test_cache_miss(self):
        """Test cache miss returns None."""
        cache = AMICacheService()

        result = cache.get("nonexistent-key")
        assert result is None

        stats = cache.get_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 1
        assert stats["total_requests"] == 1

    def test_cache_expiration(self):
        """Test TTL-based expiration."""
        cache = AMICacheService(ttl_seconds=1)  # 1 second TTL
        cache.set("test-key", "ami-12345678")

        # Should hit immediately
        assert cache.get("test-key") == "ami-12345678"

        # Wait for expiration
        time.sleep(1.1)

        # Should miss after expiration
        assert cache.get("test-key") is None

    def test_cache_size_limit(self):
        """Test cache evicts oldest entries when full."""
        cache = AMICacheService(max_entries=2)

        cache.set("key1", "ami-1")
        cache.set("key2", "ami-2")
        cache.set("key3", "ami-3")  # Should evict key1

        assert cache.get("key1") is None  # Evicted
        assert cache.get("key2") == "ami-2"
        assert cache.get("key3") == "ami-3"

        stats = cache.get_stats()
        assert stats["evictions"] == 1

    def test_failed_tracking(self):
        """Test failed entry tracking."""
        cache = AMICacheService(ttl_seconds=60)

        cache.mark_failed("failed-key")
        assert cache.is_failed("failed-key") is True
        assert cache.is_failed("other-key") is False

    def test_failed_expiration(self):
        """Test failed entries expire."""
        cache = AMICacheService(ttl_seconds=1)

        cache.mark_failed("failed-key")
        assert cache.is_failed("failed-key") is True

        time.sleep(1.1)
        assert cache.is_failed("failed-key") is False

    def test_stale_cache_access(self):
        """Test accessing stale cache entries."""
        cache = AMICacheService(ttl_seconds=1)
        cache.set("test-key", "ami-12345678")

        time.sleep(1.1)  # Expire entry

        # Normal get should return None
        assert cache.get("test-key") is None

        # Stale get should return expired value
        assert cache.get_stale("test-key") == "ami-12345678"

    def test_cache_clear(self):
        """Test cache clearing."""
        cache = AMICacheService()
        cache.set("key1", "ami-1")
        cache.mark_failed("failed-key")

        cache.clear()

        assert cache.get("key1") is None
        assert cache.is_failed("failed-key") is False
        assert cache.get_stats()["cache_size"] == 0

    def test_clear_expired_entries(self):
        """Test manual expired entry removal."""
        cache = AMICacheService(ttl_seconds=1)
        cache.set("key1", "ami-1")
        cache.mark_failed("failed-key")

        time.sleep(1.1)

        removed_count = cache.clear_expired()
        assert removed_count == 2  # One cache entry + one failed entry
