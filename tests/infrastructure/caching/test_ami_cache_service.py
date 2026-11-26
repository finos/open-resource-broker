"""Tests for infrastructure AMI cache service."""

from unittest.mock import patch

import pytest

from orb.infrastructure.caching.ami_cache_service import AMICacheService
from tests.shared.manual_clock import ManualClock


@pytest.fixture
def clock():
    """Replace ``time`` inside the cache module with a manually advanced clock."""
    fake = ManualClock()
    with patch("orb.infrastructure.caching.ami_cache_service.time", fake):
        yield fake


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

    def test_cache_expiration(self, clock):
        """Test TTL-based expiration."""
        cache = AMICacheService(ttl_seconds=10)
        cache.set("test-key", "ami-12345678")

        # Still a hit one second before the TTL
        clock.advance(9)
        assert cache.get("test-key") == "ami-12345678"

        # A miss once the full TTL has elapsed
        clock.advance(1)
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

    def test_failed_expiration(self, clock):
        """Test failed entries expire."""
        cache = AMICacheService(ttl_seconds=10)

        cache.mark_failed("failed-key")
        clock.advance(9)
        assert cache.is_failed("failed-key") is True

        clock.advance(1)
        assert cache.is_failed("failed-key") is False

    def test_stale_cache_access(self, clock):
        """Test accessing stale cache entries."""
        cache = AMICacheService(ttl_seconds=1)
        cache.set("test-key", "ami-12345678")

        clock.advance(1)  # Expire entry

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

    def test_clear_expired_entries(self, clock):
        """Test manual expired entry removal."""
        cache = AMICacheService(ttl_seconds=10)
        cache.set("old", "ami-old")
        cache.mark_failed("old-failed")
        clock.advance(5)
        cache.set("new", "ami-new")
        cache.mark_failed("new-failed")
        clock.advance(5)  # "old" entries are exactly one TTL old

        removed_count = cache.clear_expired()

        assert removed_count == 2  # One cache entry + one failed entry
        stats = cache.get_stats()
        assert stats["cache_size"] == 1
        assert stats["failed_size"] == 1
        assert cache.get("new") == "ami-new"
        assert cache.is_failed("new-failed") is True
        assert cache.get_stale("old") is None
        assert cache.is_failed("old-failed") is False
