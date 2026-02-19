#!/usr/bin/env python3
"""Simple performance test for AMI resolver caching."""

import time
from unittest.mock import patch, MagicMock
from src.providers.aws.domain.services.ami_resolver import AWSAMIResolver


def test_caching_performance():
    """Test that caching improves performance."""
    print("Testing AMI resolver caching performance...")
    
    # Mock SSM client with delay to simulate network latency
    def mock_get_parameter_with_delay(Name):
        time.sleep(0.1)  # Simulate 100ms network delay
        return {'Parameter': {'Value': 'ami-performance123'}}
    
    with patch('boto3.client') as mock_boto3:
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.side_effect = mock_get_parameter_with_delay
        mock_boto3.return_value = mock_ssm
        
        resolver = AWSAMIResolver()
        ssm_param = "/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2"
        
        # Test without cache (first call)
        start_time = time.time()
        result1 = resolver.resolve_image_id(ssm_param)
        first_call_time = time.time() - start_time
        
        # Test with cache (second call)
        start_time = time.time()
        result2 = resolver.resolve_image_id(ssm_param)
        second_call_time = time.time() - start_time
        
        # Verify results
        assert result1 == result2 == "ami-performance123"
        assert mock_ssm.get_parameter.call_count == 1  # Only one AWS call
        
        # Calculate performance improvement
        improvement = (first_call_time - second_call_time) / first_call_time * 100
        
        print(f"First call (AWS API): {first_call_time:.3f}s")
        print(f"Second call (cache): {second_call_time:.3f}s")
        print(f"Performance improvement: {improvement:.1f}%")
        print(f"AWS API calls: {mock_ssm.get_parameter.call_count}")
        
        # Get cache statistics
        stats = resolver.get_cache_stats()
        print(f"Cache stats: {stats}")
        
        assert improvement > 50, f"Expected >50% improvement, got {improvement:.1f}%"
        print("✅ Caching performance test passed!")


def test_cache_statistics():
    """Test cache statistics functionality."""
    print("\nTesting cache statistics...")
    
    resolver = AWSAMIResolver()
    
    # Add some cache activity
    resolver._cache.set("key1", "ami-1")
    resolver._cache.set("key2", "ami-2")
    resolver._cache.mark_failed("failed-key")
    
    # Test cache hits and misses
    resolver._cache.get("key1")  # Hit
    resolver._cache.get("key2")  # Hit
    resolver._cache.get("nonexistent")  # Miss
    
    stats = resolver.get_cache_stats()
    print(f"Cache statistics: {stats}")
    
    assert stats['cache_enabled'] is True
    assert stats['cache_size'] == 2
    assert stats['failed_size'] == 1
    assert stats['hits'] == 2
    assert stats['misses'] == 1
    assert stats['hit_rate'] == 2/3
    
    print("✅ Cache statistics test passed!")


def test_persistent_cache():
    """Test persistent cache functionality."""
    print("\nTesting persistent cache...")
    
    import tempfile
    import os
    
    with tempfile.TemporaryDirectory() as temp_dir:
        cache_file = os.path.join(temp_dir, "test_cache.json")
        config = {
            'cache': {
                'persistent': True,
                'file_path': cache_file
            }
        }
        
        # Create resolver and add cache entry
        resolver1 = AWSAMIResolver(config)
        resolver1._cache.set("persistent-key", "ami-persistent123")
        resolver1._save_persistent_cache()
        
        # Verify cache file was created
        assert os.path.exists(cache_file)
        print(f"Cache file created: {cache_file}")
        
        # Create new resolver and verify cache loaded
        resolver2 = AWSAMIResolver(config)
        cached_value = resolver2._cache.get("persistent-key")
        assert cached_value == "ami-persistent123"
        
        print("✅ Persistent cache test passed!")


if __name__ == "__main__":
    test_caching_performance()
    test_cache_statistics()
    test_persistent_cache()
    print("\n🎉 All caching tests passed successfully!")