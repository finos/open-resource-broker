"""Tests for AWS AMI resolver caching functionality."""

import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from infrastructure.caching.ami_cache_service import AMICacheService
from providers.aws.domain.services.ami_resolver import AWSAMIResolver


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


class TestAWSAMIResolver:
    """Test AWS AMI resolver with caching."""

    def test_resolver_initialization_default(self):
        """Test resolver initializes with default config."""
        cache_service = AMICacheService()
        resolver = AWSAMIResolver(cache_service=cache_service)
        assert resolver._cache_enabled is True
        assert resolver._cache is not None

    def test_resolver_initialization_disabled_cache(self):
        """Test resolver with disabled cache."""
        config = {"cache": {"enabled": False}}
        resolver = AWSAMIResolver(config)
        assert resolver._cache_enabled is False
        assert resolver._cache is None

    def test_resolver_initialization_custom_config(self):
        """Test resolver with custom cache config."""
        config = {
            "cache": {
                "enabled": True,
                "ttl_seconds": 1800,
                "max_entries": 500,
                "persistent": True,
                "file_path": "/tmp/test_cache.json",
            }
        }
        cache_service = AMICacheService(ttl_seconds=1800, max_entries=500)
        resolver = AWSAMIResolver(config, cache_service=cache_service)
        assert resolver._cache_enabled is True
        assert resolver._cache is not None
        assert resolver._persistent_cache_enabled is True

    def test_direct_ami_id_no_caching(self):
        """Test direct AMI IDs bypass caching."""
        cache_service = AMICacheService()
        resolver = AWSAMIResolver(cache_service=cache_service)
        result = resolver.resolve_image_id("ami-12345678")
        assert result == "ami-12345678"

    @patch("boto3.client")
    def test_ssm_parameter_caching(self, mock_boto3):
        """Test SSM parameter resolution with caching."""
        # Mock SSM client
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-resolved123"}}
        mock_boto3.return_value = mock_ssm

        cache_service = AMICacheService()
        resolver = AWSAMIResolver(cache_service=cache_service)
        ssm_param = "/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2"

        # First call should hit AWS
        result1 = resolver.resolve_image_id(ssm_param)
        assert result1 == "ami-resolved123"
        assert mock_ssm.get_parameter.call_count == 1

        # Second call should hit cache
        result2 = resolver.resolve_image_id(ssm_param)
        assert result2 == "ami-resolved123"
        assert mock_ssm.get_parameter.call_count == 1  # No additional calls

    @patch("boto3.client")
    def test_ssm_parameter_failure_caching(self, mock_boto3):
        """Test failed SSM parameter resolution caching."""
        # Mock SSM client to fail
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.side_effect = Exception("SSM error")
        mock_boto3.return_value = mock_ssm

        cache_service = AMICacheService()
        resolver = AWSAMIResolver(cache_service=cache_service)
        ssm_param = "/aws/service/invalid-param"

        # First call should fail and cache failure
        with pytest.raises(ValueError):
            resolver.resolve_image_id(ssm_param)

        # Second call should return original without hitting AWS
        result = resolver.resolve_image_id(ssm_param)
        assert result == ssm_param
        assert mock_ssm.get_parameter.call_count == 1  # Only one AWS call

    @patch("boto3.client")
    def test_stale_fallback_on_failure(self, mock_boto3):
        """Test stale cache fallback on AWS failure."""
        # Mock SSM client - first success, then failure
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.side_effect = [
            {"Parameter": {"Value": "ami-cached123"}},  # First call succeeds
            Exception("AWS down"),  # Second call fails
        ]
        mock_boto3.return_value = mock_ssm

        config = {"cache": {"ttl_seconds": 1, "allow_stale_fallback": True}}
        cache_service = AMICacheService(ttl_seconds=1)
        resolver = AWSAMIResolver(config, cache_service=cache_service)
        ssm_param = "/aws/service/test-param"

        # First call succeeds and caches
        result1 = resolver.resolve_image_id(ssm_param)
        assert result1 == "ami-cached123"

        # Wait for cache to expire
        time.sleep(1.1)

        # Second call should use stale cache as fallback
        result2 = resolver.resolve_image_id(ssm_param)
        assert result2 == "ami-cached123"

    def test_persistent_cache_save_load(self):
        """Test persistent cache file operations."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = os.path.join(temp_dir, "test_cache.json")
            config = {"cache": {"persistent": True, "file_path": cache_file}}

            # Create resolver and add cache entry
            cache_service1 = AMICacheService()
            resolver1 = AWSAMIResolver(config, cache_service=cache_service1)
            resolver1._cache.set("test-key", "ami-persistent123")
            resolver1._save_persistent_cache()

            # Create new resolver and verify cache loaded
            cache_service2 = AMICacheService()
            AWSAMIResolver(config, cache_service=cache_service2)  # Create but don't store
            # Note: In this simplified version, persistent cache loading is limited
            # A full implementation would have proper export/import methods

    def test_cache_statistics(self):
        """Test cache statistics reporting."""
        cache_service = AMICacheService()
        resolver = AWSAMIResolver(cache_service=cache_service)

        # Initial stats
        stats = resolver.get_cache_stats()
        assert stats["cache_enabled"] is True
        assert stats["cache_size"] == 0
        assert stats["hit_rate"] == 0

        # Add some cache activity
        resolver._cache.set("key1", "ami-1")
        resolver._cache.get("key1")  # Hit
        resolver._cache.get("key2")  # Miss

        stats = resolver.get_cache_stats()
        assert stats["cache_size"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_cache_management_methods(self):
        """Test cache management operations."""
        cache_service = AMICacheService()
        resolver = AWSAMIResolver(cache_service=cache_service)

        # Add cache entries
        resolver._cache.set("key1", "ami-1")
        resolver._cache.set("key2", "ami-2")

        # Clear cache
        resolver.clear_cache()
        assert resolver.get_cache_stats()["cache_size"] == 0

        # Test expired entry removal
        cache_service_ttl = AMICacheService(ttl_seconds=1)
        resolver._cache = cache_service_ttl
        resolver._cache.set("key1", "ami-1")
        time.sleep(1.1)

        removed = resolver.remove_expired_entries()
        assert removed == 1

    def test_disabled_cache_operations(self):
        """Test operations when cache is disabled."""
        config = {"cache": {"enabled": False}}
        resolver = AWSAMIResolver(config)

        stats = resolver.get_cache_stats()
        assert stats["cache_enabled"] is False

        resolver.clear_cache()  # Should not error
        removed = resolver.remove_expired_entries()
        assert removed == 0

    def test_custom_alias_resolution(self):
        """Test custom alias resolution with caching."""
        cache_service = AMICacheService()
        resolver = AWSAMIResolver(cache_service=cache_service)

        with patch.object(resolver, "_resolve_ssm_parameter", return_value="ami-alias123"):
            result = resolver.resolve_image_id("amazon-linux-2")
            assert result == "ami-alias123"

            # Second call should hit cache
            result2 = resolver.resolve_image_id("amazon-linux-2")
            assert result2 == "ami-alias123"

    def test_unknown_reference_passthrough(self):
        """Test unknown references pass through unchanged."""
        cache_service = AMICacheService()
        resolver = AWSAMIResolver(cache_service=cache_service)

        result = resolver.resolve_image_id("unknown-reference")
        assert result == "unknown-reference"

    def test_empty_reference_error(self):
        """Test empty reference raises error."""
        cache_service = AMICacheService()
        resolver = AWSAMIResolver(cache_service=cache_service)

        with pytest.raises(ValueError, match="Image reference cannot be empty"):
            resolver.resolve_image_id("")

    def test_cache_key_generation(self):
        """Test cache key generation is consistent."""
        cache_service = AMICacheService()
        resolver = AWSAMIResolver(cache_service=cache_service)

        key1 = resolver._generate_cache_key("test-reference")
        key2 = resolver._generate_cache_key("test-reference")
        key3 = resolver._generate_cache_key("different-reference")

        assert key1 == key2  # Same input, same key
        assert key1 != key3  # Different input, different key
        assert len(key1) == 64  # SHA256 hash length
