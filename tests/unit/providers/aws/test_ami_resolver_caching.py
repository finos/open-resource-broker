"""Unit tests proving AWSAMIResolver uses AMICacheService for cache hits."""

from unittest.mock import MagicMock, patch

import pytest

from orb.infrastructure.caching.ami_cache_service import AMICacheService
from orb.providers.aws.domain.services.ami_resolver import AWSAMIResolver


@pytest.mark.unit
@pytest.mark.providers
class TestAWSAMIResolverCaching:
    """Verify that AWSAMIResolver caches AMI lookups via AMICacheService."""

    def test_cache_hit_skips_aws_lookup(self):
        """Second call with same SSM path returns cached AMI without a second AWS call."""
        cache = AMICacheService()
        resolver = AWSAMIResolver(cache_service=cache)

        ssm_path = "/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2"
        expected_ami = "ami-0abcdef1234567890"

        # Patch the private SSM resolution method so no real boto call happens.
        with patch.object(
            resolver, "_resolve_ssm_parameter", return_value=expected_ami
        ) as mock_ssm:
            # First call — should hit AWS
            result1 = resolver.resolve_image_id(ssm_path)
            assert result1 == expected_ami
            assert mock_ssm.call_count == 1

            # Second call — should be served from cache, no second AWS call
            result2 = resolver.resolve_image_id(ssm_path)
            assert result2 == expected_ami
            assert mock_ssm.call_count == 1, (
                "Expected only 1 SSM call total; a second call means the cache was not used"
            )

    def test_cache_stats_reflect_hit(self):
        """Cache statistics correctly track a hit after the second lookup."""
        cache = AMICacheService()
        resolver = AWSAMIResolver(cache_service=cache)

        ssm_path = "/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2"
        ami_id = "ami-0abcdef1234567890"

        with patch.object(resolver, "_resolve_ssm_parameter", return_value=ami_id):
            resolver.resolve_image_id(ssm_path)  # miss
            resolver.resolve_image_id(ssm_path)  # hit

        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    def test_direct_ami_id_bypasses_cache(self):
        """Direct ami-* IDs are returned as-is without touching the cache service."""
        mock_cache = MagicMock(spec=AMICacheService)
        resolver = AWSAMIResolver(cache_service=mock_cache)

        result = resolver.resolve_image_id("ami-0123456789abcdef0")

        assert result == "ami-0123456789abcdef0"
        mock_cache.get.assert_not_called()
        mock_cache.set.assert_not_called()
