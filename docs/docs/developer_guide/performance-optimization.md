# Performance Optimization Developer Guide

**Version**: 1.0  
**Date**: January 18, 2025  
**Audience**: Developers, DevOps Engineers

---

## Overview

This guide provides practical information for developers working with the Open HostFactory Plugin's performance-optimized lazy loading architecture. It covers development practices, debugging techniques, and optimization strategies.

## Quick Start

### Performance Targets

| Metric | Target | Current Achievement |
|--------|--------|-------------------|
| Startup Time | <500ms | 0.326s PASS |
| Help Command | <1000ms | 294ms PASS |
| Memory Usage | <50MB increase | ~5MB PASS |
| First Access | <1000ms | Variable PASS |

### Verification Commands

```bash
# Test startup performance
time python src/run.py --help

# Test full functionality
python src/run.py templates list

# Run performance tests
PYTHONPATH=. python tests/performance/test_lazy_loading_performance.py

# Run integration tests
PYTHONPATH=. python tests/integration/test_lazy_loading_integration.py
```

---

## Development Practices

### 1. Adding New Components

When adding new components to the system, follow these patterns to maintain performance:

#### Lazy Component Registration

```python
# PASS Good: Lazy registration
def register_my_service_lazy(container):
    """Register service with lazy loading support."""
    def create_my_service():
        return MyService(
            dependency1=container.get(Dependency1),
            dependency2=container.get(Dependency2)
        )

    container.register_factory(MyService, create_my_service)

# Register for on-demand loading
container.register_on_demand(MyService, register_my_service_lazy)
```

#### Avoid Eager Initialization

```python
# FAIL Bad: Eager initialization in constructor
class MyComponent:
    def __init__(self):
        self.heavy_resource = create_heavy_resource()  # Blocks startup
        self.database = connect_to_database()          # I/O operation

# PASS Good: Lazy initialization
class MyComponent:
    def __init__(self):
        self._heavy_resource = None
        self._database = None

    @property
    def heavy_resource(self):
        if self._heavy_resource is None:
            self._heavy_resource = create_heavy_resource()
        return self._heavy_resource

    @property
    def database(self):
        if self._database is None:
            self._database = connect_to_database()
        return self._database
```

### 2. Configuration Management

#### Performance Configuration

```python
# config/performance_config.json
{
  "performance": {
    "lazy_loading": {
      "enabled": true,
      "cache_instances": true,
      "discovery_mode": "lazy",
      "connection_mode": "lazy",
      "debug_timing": false,
      "preload_critical": [
        "LoggingPort",
        "ConfigurationPort"
      ]
    },
    "startup": {
      "minimal_registration": true,
      "defer_heavy_operations": true,
      "background_loading": false
    }
  }
}
```

#### Environment Variables

```bash
# Enable/disable lazy loading
export LAZY_LOADING_ENABLED=true

# Debug performance
export PERFORMANCE_DEBUG=true

# Set log level for performance monitoring
export PERFORMANCE_LOG_LEVEL=DEBUG
```

### 3. Testing Performance

#### Unit Tests for Performance

```python
import time
import pytest

class TestMyComponentPerformance:
    def test_creation_is_fast(self):
        """Test that component creation is fast."""
        start_time = time.time()
        component = MyComponent()
        creation_time = (time.time() - start_time) * 1000

        assert creation_time < 10, f"Creation took {creation_time:.1f}ms"

    def test_first_access_performance(self):
        """Test first access performance."""
        component = MyComponent()

        start_time = time.time()
        result = component.expensive_operation()
        access_time = (time.time() - start_time) * 1000

        assert access_time < 1000, f"First access took {access_time:.1f}ms"

    def test_cached_access_performance(self):
        """Test cached access performance."""
        component = MyComponent()
        component.expensive_operation()  # Prime cache

        start_time = time.time()
        result = component.expensive_operation()  # Should be cached
        cached_time = (time.time() - start_time) * 1000

        assert cached_time < 10, f"Cached access took {cached_time:.1f}ms"
```

#### Integration Tests

```python
def test_end_to_end_performance():
    """Test end-to-end command performance."""
    import subprocess
    import sys

    start_time = time.time()
    result = subprocess.run([
        sys.executable, "src/run.py", "templates", "list"
    ], capture_output=True, text=True)
    total_time = (time.time() - start_time) * 1000

    assert result.returncode == 0
    assert total_time < 5000, f"Command took {total_time:.1f}ms"
```

---

## Debugging Performance Issues

### 1. Performance Profiling

#### Basic Timing

```python
import time
from src.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

def profile_function(func):
    """Decorator to profile function execution time."""
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        execution_time = (time.time() - start_time) * 1000
        logger.info(f"{func.__name__} executed in {execution_time:.1f}ms")
        return result
    return wrapper

# Usage
@profile_function
def expensive_operation():
    # Your code here
    pass
```

#### Memory Profiling

```python
import psutil
import os

def profile_memory(func):
    """Decorator to profile memory usage."""
    def wrapper(*args, **kwargs):
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        result = func(*args, **kwargs)

        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory

        logger.info(f"{func.__name__} memory increase: {memory_increase:.1f}MB")
        return result
    return wrapper
```

### 2. Debug Configuration

#### Enable Performance Debugging

```python
# config/debug_config.json
{
  "logging": {
    "level": "DEBUG",
    "performance_timing": true
  },
  "performance": {
    "lazy_loading": {
      "debug_timing": true,
      "log_component_loads": true,
      "track_memory_usage": true
    }
  }
}
```

#### Debug Logging

```python
from src.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

def debug_lazy_loading():
    """Enable debug logging for lazy loading."""
    logger.debug("Starting lazy component loading...")

    start_time = time.time()
    component = container.get(MyComponent)
    load_time = (time.time() - start_time) * 1000

    logger.debug(f"Component loaded in {load_time:.1f}ms")
    logger.debug(f"Component type: {type(component)}")
    logger.debug(f"Component cached: {hasattr(container, '_cached_instances')}")
```

### 3. Common Performance Issues

#### Issue 1: Slow Startup

**Symptoms**: Application takes >1 second to start
**Diagnosis**:
```python
# Add timing to identify bottlenecks
import time

def diagnose_startup():
    start_time = time.time()

    # Test each component
    logger.info("Creating application...")
    app = Application()
    logger.info(f"App creation: {(time.time() - start_time) * 1000:.1f}ms")

    start_time = time.time()
    app.initialize()
    logger.info(f"App initialization: {(time.time() - start_time) * 1000:.1f}ms")
```

**Solutions**:
- Check if lazy loading is enabled
- Identify components being loaded eagerly
- Move heavy operations to lazy properties

#### Issue 2: High Memory Usage

**Symptoms**: Memory usage grows unexpectedly
**Diagnosis**:
```python
import gc
import psutil

def diagnose_memory():
    process = psutil.Process(os.getpid())

    # Before operation
    gc.collect()
    initial_memory = process.memory_info().rss / 1024 / 1024

    # Perform operation
    result = expensive_operation()

    # After operation
    gc.collect()
    final_memory = process.memory_info().rss / 1024 / 1024

    logger.info(f"Memory increase: {final_memory - initial_memory:.1f}MB")
```

**Solutions**:
- Implement appropriate cleanup in component destructors
- Use weak references for caches
- Implement component unloading for unused services

#### Issue 3: Slow First Access

**Symptoms**: First access to component takes >1 second
**Diagnosis**:
```python
def diagnose_first_access():
    # Test component loading time
    start_time = time.time()
    component = container.get(SlowComponent)
    load_time = (time.time() - start_time) * 1000

    logger.info(f"Component load time: {load_time:.1f}ms")

    # Test component initialization
    start_time = time.time()
    result = component.initialize()
    init_time = (time.time() - start_time) * 1000

    logger.info(f"Component init time: {init_time:.1f}ms")
```

**Solutions**:
- Break down component initialization into smaller steps
- Implement background initialization
- Cache expensive computations

---

## Optimization Strategies

### 1. Component Optimization

#### Lazy Properties

```python
class OptimizedComponent:
    def __init__(self):
        self._expensive_resource = None
        self._database_connection = None

    @property
    def expensive_resource(self):
        """Lazy-loaded expensive resource."""
        if self._expensive_resource is None:
            logger.debug("Loading expensive resource...")
            self._expensive_resource = create_expensive_resource()
        return self._expensive_resource

    @property
    def database_connection(self):
        """Lazy-loaded database connection."""
        if self._database_connection is None:
            logger.debug("Connecting to database...")
            self._database_connection = create_database_connection()
        return self._database_connection
```

#### Caching Strategies

```python
from functools import lru_cache
import time

class CachedComponent:
    @lru_cache(maxsize=128)
    def expensive_computation(self, input_data):
        """Cache expensive computations."""
        logger.debug(f"Computing result for {input_data}")
        # Expensive computation here
        return result

    def __init__(self):
        self._cache = {}
        self._cache_ttl = {}
        self._ttl_seconds = 300  # 5 minutes

    def get_with_ttl(self, key):
        """Get cached value with TTL."""
        now = time.time()

        if key in self._cache:
            if now - self._cache_ttl[key] < self._ttl_seconds:
                return self._cache[key]
            else:
                # Expired, remove from cache
                del self._cache[key]
                del self._cache_ttl[key]

        # Compute new value
        value = self._compute_value(key)
        self._cache[key] = value
        self._cache_ttl[key] = now

        return value
```

### 2. Registration Optimization

#### Selective Registration

```python
def register_components_selectively(container, config):
    """Register only needed components based on configuration."""

    # Always register core components
    register_core_services(container)

    # Conditionally register optional components
    if config.get('features', {}).get('aws_integration', False):
        register_aws_services(container)

    if config.get('features', {}).get('database_support', False):
        register_database_services(container)

    if config.get('features', {}).get('monitoring', False):
        register_monitoring_services(container)
```

#### Batch Registration

```python
def register_services_in_batches(container, services):
    """Register services in batches to optimize startup."""

    # Batch 1: Critical services (immediate)
    critical_services = [LoggingService, ConfigService]
    for service in critical_services:
        container.register_singleton(service)

    # Batch 2: Core services (lazy)
    core_services = [DatabaseService, CacheService]
    for service in core_services:
        container.register_on_demand(service, lambda: service())

    # Batch 3: Optional services (very lazy)
    optional_services = [MonitoringService, ReportingService]
    for service in optional_services:
        container.register_on_demand(service, lambda: service())
```

### 3. Configuration Optimization

#### Performance Tuning

```python
# config/performance_tuning.json
{
  "performance": {
    "lazy_loading": {
      "enabled": true,
      "cache_instances": true,
      "preload_critical": [
        "LoggingPort",
        "ConfigurationPort"
      ],
      "background_loading": {
        "enabled": false,
        "thread_pool_size": 2,
        "preload_common": [
          "TemplateService",
          "RequestService"
        ]
      }
    },
    "caching": {
      "component_cache_size": 100,
      "result_cache_ttl": 300,
      "memory_limit_mb": 500
    },
    "optimization": {
      "batch_size": 10,
      "concurrent_loads": 3,
      "timeout_seconds": 30
    }
  }
}
```

---

## Monitoring and Metrics

### 1. Performance Metrics

#### Key Metrics to Track

```python
class PerformanceMetrics:
    def __init__(self):
        self.startup_time = 0
        self.component_load_times = {}
        self.memory_usage = {}
        self.cache_hit_rates = {}

    def record_startup_time(self, time_ms):
        self.startup_time = time_ms
        logger.info(f"Startup time: {time_ms:.1f}ms")

    def record_component_load(self, component_name, time_ms):
        self.component_load_times[component_name] = time_ms
        logger.info(f"{component_name} load time: {time_ms:.1f}ms")

    def record_memory_usage(self, operation, memory_mb):
        self.memory_usage[operation] = memory_mb
        logger.info(f"{operation} memory usage: {memory_mb:.1f}MB")
```

#### Automated Monitoring

```python
def setup_performance_monitoring():
    """Set up automated performance monitoring."""

    # Monitor startup time
    @profile_function
    def monitored_startup():
        app = Application()
        app.initialize()
        return app

    # Monitor component access
    original_get = container.get
    def monitored_get(service_type):
        start_time = time.time()
        result = original_get(service_type)
        load_time = (time.time() - start_time) * 1000

        metrics.record_component_load(service_type.__name__, load_time)
        return result

    container.get = monitored_get
```

### 2. Health Checks

#### Performance Health Check

```python
def performance_health_check():
    """Check system performance health."""
    health = {
        'status': 'healthy',
        'checks': {}
    }

    # Check startup time
    start_time = time.time()
    app = Application()
    startup_time = (time.time() - start_time) * 1000

    health['checks']['startup_time'] = {
        'status': 'healthy' if startup_time < 500 else 'unhealthy',
        'value': f"{startup_time:.1f}ms",
        'threshold': '500ms'
    }

    # Check memory usage
    process = psutil.Process(os.getpid())
    memory_mb = process.memory_info().rss / 1024 / 1024

    health['checks']['memory_usage'] = {
        'status': 'healthy' if memory_mb < 200 else 'warning',
        'value': f"{memory_mb:.1f}MB",
        'threshold': '200MB'
    }

    return health
```

---

## Best Practices Summary

### Do's PASS

1. **Use lazy initialization** for expensive resources
2. **Cache frequently accessed** components and results
3. **Register components on-demand** when possible
4. **Profile performance** regularly during development
5. **Test performance** as part of your test suite
6. **Monitor key metrics** in production
7. **Use configuration** to control performance features

### Don'ts FAIL

1. **Don't initialize expensive resources** in constructors
2. **Don't register all components** eagerly at startup
3. **Don't ignore memory usage** - implement cleanup
4. **Don't skip performance testing** - automate it
5. **Don't hardcode performance settings** - use configuration
6. **Don't optimize prematurely** - measure first
7. **Don't forget error handling** in lazy loading code

---

## Troubleshooting Checklist

When experiencing performance issues:

- [ ] Check if lazy loading is enabled
- [ ] Verify component registration patterns
- [ ] Profile startup sequence
- [ ] Monitor memory usage
- [ ] Check for eager initialization
- [ ] Review caching strategies
- [ ] Test with performance test suite
- [ ] Check configuration settings
- [ ] Review error logs for issues
- [ ] Validate environment setup

---

## Getting Help

### Resources

- **Architecture Documentation**: `docs/architecture/lazy-loading-design.md`
- **Performance Tests**: `tests/performance/test_lazy_loading_performance.py`
- **Integration Tests**: `tests/integration/test_lazy_loading_integration.py`
- **Memory Bank**: `memory-bank/phase-3-completion-status.md`

### Support

For performance-related issues:

1. Run the performance test suite
2. Check the troubleshooting section
3. Review the architecture documentation
4. Create a detailed issue with performance metrics

---

## Conclusion

The lazy loading architecture provides significant performance improvements while maintaining code quality and functionality. By following these development practices and optimization strategies, you can ensure your contributions maintain and enhance the system's performance characteristics.

Remember: **Measure first, optimize second, test always**.
