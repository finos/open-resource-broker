# Performance Tuning and Optimization

This guide covers performance optimization strategies for the Open Host Factory Plugin, including configuration tuning, monitoring, and troubleshooting performance issues.

## Performance Overview

The application's performance depends on several factors:
- **Lazy Loading Architecture**: Optimized startup and component loading (Phase 3 optimization)
- **Storage Strategy**: JSON vs SQL vs DynamoDB performance characteristics
- **Provider Operations**: AWS API call efficiency and batching
- **Configuration Settings**: Timeouts, batch sizes, and connection pooling
- **Resource Utilization**: Memory, CPU, and network usage
- **Concurrent Operations**: Handling multiple requests simultaneously

## Lazy Loading Performance (Phase 3 Optimizations)

### Startup Performance Optimization

The application now implements comprehensive lazy loading for optimal startup performance:

#### Performance Achievements
- **Startup Time**: Reduced from 2+ seconds to 0.326s for lightweight commands (85% improvement)
- **Memory Usage**: Significant reduction through on-demand component loading
- **Resource Efficiency**: Minimal upfront initialization with intelligent caching

#### Configuration
```json
{
  "performance": {
    "lazy_loading": {
      "enabled": true,                    // Enable lazy loading (default)
      "cache_instances": true,            // Cache created instances
      "discovery_mode": "lazy",           // Handler discovery mode
      "connection_mode": "lazy",          // Provider connection mode
      "preload_critical": [               // Services to load immediately
        "LoggingPort",
        "ConfigurationPort"
      ],
      "debug_timing": false,              // Enable performance timing logs
      "max_concurrent_loads": 5           // Maximum concurrent lazy loads
    }
  }
}
```

#### Performance Monitoring
```bash
# Test startup performance
time python src/run.py --help

# Run performance benchmarks
PYTHONPATH=. python tests/performance/test_lazy_loading_performance.py

# Monitor component loading times
export PERFORMANCE_DEBUG=true
python src/run.py templates list
```

#### Optimization Guidelines
- **Keep lazy loading enabled** for optimal startup performance
- **Use minimal registration** for non-essential components
- **Cache frequently accessed** components and results
- **Monitor performance metrics** regularly
- **Profile component loading** to identify bottlenecks

For detailed lazy loading architecture information, see:
- **[Lazy Loading Design](../architecture/lazy-loading-design.md)**: Complete architecture documentation
- **[Performance Optimization Guide](../developer_guide/performance-optimization.md)**: Developer best practices

## Storage Performance Optimization

### JSON Storage Performance

#### Single File vs Split Files
```json
{
  "storage": {
    "strategy": "json",
    "json_strategy": {
      "storage_type": "split_files",  // Better for concurrent access
      "enable_compression": true,     // Reduce disk I/O
      "cache_size": 2000,            // Increase cache for better performance
      "write_buffer_size": 128000,   // Larger buffer for batch writes
      "sync_writes": false           // Async writes for better performance
    }
  }
}
```

#### Performance Tuning
- **Use split files** for better concurrent access
- **Enable compression** to reduce disk I/O
- **Increase cache size** for frequently accessed data
- **Use larger write buffers** for batch operations
- **Disable sync writes** for non-critical data

### SQL Storage Performance

#### Connection Pooling
```json
{
  "storage": {
    "strategy": "sql",
    "sql_strategy": {
      "pool_size": 20,              // Increase for high concurrency
      "max_overflow": 30,           // Allow burst capacity
      "pool_timeout": 10,           // Reduce wait time
      "pool_recycle": 1800,         // Recycle connections regularly
      "enable_query_cache": true,   // Cache frequent queries
      "query_cache_size": 2000      // Increase cache size
    }
  }
}
```

#### Database Optimization
```sql
-- Create indexes for frequently queried fields
CREATE INDEX idx_requests_status ON requests(status);
CREATE INDEX idx_requests_created_at ON requests(created_at);
CREATE INDEX idx_machines_request_id ON machines(request_id);
CREATE INDEX idx_machines_status ON machines(status);

-- Analyze tables for query optimization
ANALYZE requests;
ANALYZE machines;
ANALYZE templates;
```

### DynamoDB Performance

#### Capacity and Scaling
```json
{
  "storage": {
    "strategy": "dynamodb",
    "dynamodb_strategy": {
      "billing_mode": "PAY_PER_REQUEST",  // Auto-scaling
      "auto_scaling": {
        "enabled": true,
        "target_utilization": 70,         // Optimal utilization
        "min_read_capacity": 5,
        "max_read_capacity": 1000,
        "min_write_capacity": 5,
        "max_write_capacity": 1000
      }
    }
  }
}
```

## Provider Performance Optimization

### AWS API Optimization

#### Batch Operations
```python
# Configure batch sizes for optimal performance
{
  "provider": {
    "aws": {
      "batch_sizes": {
        "describe_instances": 100,      // Max instances per describe call
        "terminate_instances": 50,      // Batch termination
        "create_tags": 20              // Tag creation batches
      }
    }
  }
}
```

#### Connection Optimization
```json
{
  "provider": {
    "aws": {
      "config": {
        "max_pool_connections": 100,    // Increase connection pool
        "retries": {
          "max_attempts": 3,
          "mode": "adaptive"            // Adaptive retry mode
        }
      },
      "timeouts": {
        "connect_timeout": 10,          // Reduce connection timeout
        "read_timeout": 30              // Optimize read timeout
      }
    }
  }
}
```

### Request Processing Optimization

#### Concurrent Processing
```json
{
  "processing": {
    "max_concurrent_requests": 10,      // Limit concurrent requests
    "request_queue_size": 100,          // Queue size for requests
    "worker_threads": 4,                // Number of worker threads
    "batch_processing": {
      "enabled": true,
      "batch_size": 20,                 // Process requests in batches
      "batch_timeout": 5                // Max wait time for batch
    }
  }
}
```

## Memory and Resource Optimization

### Memory Management

#### Python Memory Settings
```bash
# Set Python memory optimization flags
export PYTHONOPTIMIZE=1
export PYTHONDONTWRITEBYTECODE=1

# Configure garbage collection
export PYTHONGC=1
```

#### Application Memory Configuration
```json
{
  "performance": {
    "memory": {
      "max_cache_size": "100MB",        // Limit cache memory usage
      "gc_threshold": 1000,             // Garbage collection threshold
      "object_pool_size": 500           // Object pool for reuse
    }
  }
}
```

### CPU Optimization

#### Process Configuration
```json
{
  "performance": {
    "cpu": {
      "worker_processes": 4,            // Number of worker processes
      "thread_pool_size": 8,            // Thread pool size
      "async_operations": true,         // Enable async operations
      "cpu_affinity": [0, 1, 2, 3]     // CPU affinity for processes
    }
  }
}
```

## Monitoring and Metrics

### Performance Metrics Collection

#### Built-in Metrics
```python
from src.monitoring.metrics import MetricsCollector

# Initialize metrics collector
metrics = MetricsCollector()

# Monitor operation performance
with metrics.timer('request_processing'):
    result = process_request(request)

# Monitor resource usage
metrics.gauge('memory_usage', get_memory_usage())
metrics.counter('requests_processed').increment()
```

#### Custom Metrics
```python
# Define custom performance metrics
class PerformanceMetrics:
    def __init__(self):
        self.request_times = []
        self.error_rates = {}
        self.throughput_counter = 0

    def record_request_time(self, operation: str, duration: float):
        """Record operation duration."""
        self.request_times.append({
            'operation': operation,
            'duration': duration,
            'timestamp': time.time()
        })

    def get_average_response_time(self, operation: str = None) -> float:
        """Calculate average response time."""
        if operation:
            times = [r['duration'] for r in self.request_times 
                    if r['operation'] == operation]
        else:
            times = [r['duration'] for r in self.request_times]

        return sum(times) / len(times) if times else 0.0
```

### Performance Monitoring Dashboard

#### Key Performance Indicators
- **Response Time**: Average, 95th percentile, 99th percentile
- **Throughput**: Requests per second, operations per minute
- **Error Rate**: Percentage of failed operations
- **Resource Utilization**: CPU, memory, disk I/O
- **Queue Depth**: Pending requests and operations

#### Monitoring Configuration
```json
{
  "monitoring": {
    "performance": {
      "enabled": true,
      "collection_interval": 30,        // Collect metrics every 30 seconds
      "retention_period": "7d",         // Keep metrics for 7 days
      "alerts": {
        "response_time_threshold": 5.0, // Alert if response time > 5s
        "error_rate_threshold": 0.05,   // Alert if error rate > 5%
        "memory_usage_threshold": 0.8   // Alert if memory usage > 80%
      }
    }
  }
}
```

## Performance Testing

### Load Testing

#### Test Configuration
```python
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

async def load_test_requests():
    """Load test request processing."""

    # Test parameters
    concurrent_requests = 50
    total_requests = 1000

    # Create test requests
    test_requests = [
        {"template_id": f"test-template-{i}", "machine_count": 1}
        for i in range(total_requests)
    ]

    # Execute concurrent requests
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=concurrent_requests) as executor:
        futures = [
            executor.submit(process_request, request)
            for request in test_requests
        ]

        # Wait for completion
        results = [future.result() for future in futures]

    end_time = time.time()

    # Calculate performance metrics
    duration = end_time - start_time
    throughput = total_requests / duration

    print(f"Processed {total_requests} requests in {duration:.2f}s")
    print(f"Throughput: {throughput:.2f} requests/second")
```

#### Benchmark Results
```bash
# Run performance benchmarks
python scripts/performance_test.py

# Example output:
# Storage Strategy: JSON
# - Average response time: 0.15s
# - 95th percentile: 0.25s
# - Throughput: 67 requests/second
# 
# Storage Strategy: SQL
# - Average response time: 0.08s
# - 95th percentile: 0.12s
# - Throughput: 125 requests/second
```

## Troubleshooting Performance Issues

### Common Performance Problems

#### Slow Response Times
```bash
# Check database performance
python -c "
from src.monitoring.performance import analyze_slow_queries
slow_queries = analyze_slow_queries()
for query in slow_queries:
    print(f'Query: {query[\"sql\"]}')
    print(f'Duration: {query[\"duration\"]}s')
"

# Check AWS API performance
python -c "
from src.monitoring.performance import analyze_aws_performance
aws_metrics = analyze_aws_performance()
print(f'Average API response time: {aws_metrics[\"avg_response_time\"]}s')
print(f'API error rate: {aws_metrics[\"error_rate\"]}%')
"
```

#### High Memory Usage
```bash
# Monitor memory usage
python -c "
import psutil
import os

process = psutil.Process(os.getpid())
memory_info = process.memory_info()
print(f'RSS: {memory_info.rss / 1024 / 1024:.2f} MB')
print(f'VMS: {memory_info.vms / 1024 / 1024:.2f} MB')
"

# Check for memory leaks
python -c "
from src.monitoring.memory import check_memory_leaks
leaks = check_memory_leaks()
if leaks:
    print('Memory leaks detected:')
    for leak in leaks:
        print(f'  {leak}')
else:
    print('No memory leaks detected')
"
```

#### High CPU Usage
```bash
# Profile CPU usage
python -m cProfile -o profile_output.prof run.py getAvailableTemplates

# Analyze profile
python -c "
import pstats
stats = pstats.Stats('profile_output.prof')
stats.sort_stats('cumulative')
stats.print_stats(10)  # Top 10 functions by cumulative time
"
```

### Performance Optimization Checklist

#### Configuration Optimization
- [ ] Optimize storage strategy for your use case
- [ ] Configure appropriate connection pool sizes
- [ ] Set optimal batch sizes for operations
- [ ] Enable caching where appropriate
- [ ] Configure timeouts for your environment

#### Code Optimization
- [ ] Use async operations where possible
- [ ] Implement proper connection pooling
- [ ] Optimize database queries with indexes
- [ ] Use batch operations for bulk data
- [ ] Implement proper error handling and retries

#### Infrastructure Optimization
- [ ] Ensure adequate CPU and memory resources
- [ ] Use SSD storage for better I/O performance
- [ ] Configure network settings for optimal throughput
- [ ] Monitor and tune garbage collection
- [ ] Use appropriate Python version and settings

## Performance Best Practices

### General Guidelines

1. **Choose the Right Storage Strategy**
   - JSON for small datasets and simple deployments
   - SQL for medium datasets with complex queries
   - DynamoDB for large-scale, high-availability deployments

2. **Optimize Configuration**
   - Tune connection pools based on concurrency needs
   - Set appropriate timeouts for your environment
   - Use batch operations for bulk data processing

3. **Monitor Continuously**
   - Collect performance metrics regularly
   - Set up alerts for performance degradation
   - Analyze trends to identify optimization opportunities

4. **Test Under Load**
   - Perform regular load testing
   - Test with realistic data volumes
   - Validate performance under failure conditions

5. **Plan for Scale**
   - Design for horizontal scaling
   - Use appropriate cloud services for scale
   - Monitor resource utilization and plan capacity

## Next Steps

- **[Monitoring](../user_guide/monitoring.md)**: Set up comprehensive monitoring
- **[Backup and Recovery](backup_recovery.md)**: Implement backup strategies
- **[Troubleshooting](../user_guide/troubleshooting.md)**: Diagnose and fix issues
- **[Configuration](../configuration-guide.md)**: Optimize configuration settings
