#  **Provider Strategy Pattern - Complete Usage Guide**

##  **Overview**

The Provider Strategy Pattern enables runtime provider switching, composition, and advanced provider management. This guide covers everything you need to know about using, extending, and configuring the provider strategy system.

---

##  **Architecture Overview**

```
Provider Strategy Pattern Architecture:
+--- Core Strategy Pattern (src/providers/base/strategy/)
|   +--- ProviderStrategy - Abstract base class for all providers
|   +--- ProviderContext - Manages and executes strategies
|   +--- ProviderSelector - Algorithms for strategy selection
|   +--- CompositeProviderStrategy - Multi-provider composition
|   +--- FallbackProviderStrategy - Resilience and failover
|   +--- LoadBalancingProviderStrategy - Performance optimization
+--- Provider Implementations (src/providers/{provider}/strategy/)
|   +--- AWSProviderStrategy - AWS cloud provider
|   +--- Provider1Strategy - Generic provider example
|   +--- Provider2Strategy - Another generic provider example
+--- Application Integration
    +--- StrategyIntegratedApplicationService - Main service interface
```

---

##  **Quick Start**

### **1. Basic Provider Strategy Usage**

```python
from src.providers.base.strategy import (
    create_provider_context,
    ProviderOperation,
    ProviderOperationType
)
from src.providers.aws.strategy import AWSProviderStrategy
from src.providers.aws.configuration.config import AWSConfig

# Create provider context
context = create_provider_context()

# Create and register AWS strategy
aws_config = AWSConfig(region='us-east-1', profile='default')
aws_strategy = AWSProviderStrategy(aws_config)
context.register_strategy(aws_strategy)

# Initialize context
context.initialize()

# Execute operations
operation = ProviderOperation(
    operation_type=ProviderOperationType.HEALTH_CHECK,
    parameters={}
)

result = context.execute_operation(operation)
print(f"Health check result: {result.success}")
```

### **2. Runtime Provider Switching**

```python
# Register multiple providers
context.register_strategy(aws_strategy)
context.register_strategy(provider1_strategy)
context.register_strategy(provider2_strategy)

# Switch between providers at runtime
context.set_strategy('aws')
result1 = context.execute_operation(operation)

context.set_strategy('provider1')
result2 = context.execute_operation(operation)

# Check current active strategy
current = context.current_strategy_type
print(f"Currently using: {current}")
```

---

##  **Creating New Providers**

### **Step 1: Implement ProviderStrategy Interface**

```python
# src/providers/provider1/strategy/provider1_strategy.py
from src.providers.base.strategy import (
    ProviderStrategy,
    ProviderOperation,
    ProviderResult,
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperationType
)

class Provider1Strategy(ProviderStrategy):
    """Generic Provider1 implementation of ProviderStrategy."""

    def __init__(self, config: Provider1Config, logger=None):
        super().__init__(config)
        self._config = config
        self._logger = logger or get_logger(__name__)

    @property
    def provider_type(self) -> str:
        return "provider1"

    def initialize(self) -> bool:
        """Initialize Provider1 connections and resources."""
        try:
            # Initialize Provider1 client/SDK
            self._client = Provider1Client(self._config)
            self._initialized = True
            return True
        except Exception as e:
            self._logger.error(f"Provider1 initialization failed: {e}")
            return False

    def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """Execute operation using Provider1 services."""
        if operation.operation_type == ProviderOperationType.CREATE_INSTANCES:
            return self._create_instances(operation)
        elif operation.operation_type == ProviderOperationType.TERMINATE_INSTANCES:
            return self._terminate_instances(operation)
        elif operation.operation_type == ProviderOperationType.HEALTH_CHECK:
            return self._health_check(operation)
        # ... implement other operations

        return ProviderResult.error_result(
            f"Unsupported operation: {operation.operation_type}",
            "UNSUPPORTED_OPERATION"
        )

    def _create_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Create instances using Provider1 API."""
        try:
            template_config = operation.parameters.get('template_config', {})
            count = operation.parameters.get('count', 1)

            # Provider1-specific instance creation logic
            instances = self._client.create_instances(template_config, count)

            return ProviderResult.success_result({
                "instance_ids": [inst.id for inst in instances],
                "count": len(instances)
            })
        except Exception as e:
            return ProviderResult.error_result(
                f"Provider1 instance creation failed: {str(e)}",
                "CREATE_INSTANCES_ERROR"
            )

    def get_capabilities(self) -> ProviderCapabilities:
        """Get Provider1 capabilities."""
        return ProviderCapabilities(
            provider_type="provider1",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.HEALTH_CHECK
            ],
            features={
                "instance_management": True,
                "auto_scaling": True,
                "load_balancing": False,
                "regions": ["region1", "region2"],
                "max_instances_per_request": 50
            }
        )

    def check_health(self) -> ProviderHealthStatus:
        """Check Provider1 service health."""
        try:
            # Provider1-specific health check
            response = self._client.ping()
            return ProviderHealthStatus.healthy(
                f"Provider1 healthy - Response time: {response.time}ms"
            )
        except Exception as e:
            return ProviderHealthStatus.unhealthy(
                f"Provider1 health check failed: {str(e)}"
            )
```

### **Step 2: Create Provider Configuration**

```python
# src/providers/provider1/configuration/config.py
from pydantic import BaseModel
from typing import Optional

class Provider1Config(BaseModel):
    """Configuration for Provider1."""
    endpoint_url: str
    api_key: str
    region: str = "region1"
    timeout: int = 30
    max_retries: int = 3

    class Config:
        extra = "allow"
```

### **Step 3: Register Provider in DI Container**

```python
# In src/infrastructure/di/services.py
def register_provider1_strategy(container):
    """Register Provider1 strategy."""
    def create_provider1_strategy(container):
        from src.providers.provider1.strategy import Provider1Strategy
        from src.providers.provider1.configuration.config import Provider1Config

        config_data = container.get(ConfigurationPort).get_provider_config("provider1")
        provider1_config = Provider1Config(**config_data.get("provider1", {}))

        return Provider1Strategy(provider1_config, container.get(LoggingPort))

    container.register_factory("Provider1Strategy", create_provider1_strategy)
```

---

##  **Load Balancing**

### **Basic Load Balancing Setup**

```python
from src.providers.base.strategy import (
    LoadBalancingProviderStrategy,
    LoadBalancingConfig,
    LoadBalancingAlgorithm
)

# Create load balancing configuration
lb_config = LoadBalancingConfig(
    algorithm=LoadBalancingAlgorithm.ROUND_ROBIN,
    health_check_mode=HealthCheckMode.HYBRID,
    max_connections_per_strategy=50
)

# Create load balancer with multiple strategies
load_balancer = LoadBalancingProviderStrategy(
    strategies=[aws_strategy, provider1_strategy, provider2_strategy],
    weights={"aws": 0.5, "provider1": 0.3, "provider2": 0.2},
    config=lb_config
)

# Initialize and use
load_balancer.initialize()
result = load_balancer.execute_operation(operation)
```

### **Advanced Load Balancing Algorithms**

```python
# Weighted Round Robin
lb_config = LoadBalancingConfig(
    algorithm=LoadBalancingAlgorithm.WEIGHTED_ROUND_ROBIN
)

# Least Connections
lb_config = LoadBalancingConfig(
    algorithm=LoadBalancingAlgorithm.LEAST_CONNECTIONS
)

# Adaptive (performance-based)
lb_config = LoadBalancingConfig(
    algorithm=LoadBalancingAlgorithm.ADAPTIVE,
    weight_adjustment_factor=0.1
)

# Hash-based (consistent routing)
lb_config = LoadBalancingConfig(
    algorithm=LoadBalancingAlgorithm.HASH_BASED
)
```

### **Load Balancer Monitoring**

```python
# Get real-time statistics
stats = load_balancer.strategy_stats
for strategy_type, metrics in stats.items():
    print(f"{strategy_type}:")
    print(f"  Active connections: {metrics['active_connections']}")
    print(f"  Success rate: {metrics['success_rate']:.1f}%")
    print(f"  Avg response time: {metrics['average_response_time']:.2f}ms")

# Check health of all strategies
health = load_balancer.check_health()
print(f"Load balancer health: {health.is_healthy}")
```

---

##  **Fallback and Resilience**

### **Basic Fallback Setup**

```python
from src.providers.base.strategy import (
    FallbackProviderStrategy,
    FallbackConfig,
    FallbackMode
)

# Create fallback configuration
fallback_config = FallbackConfig(
    mode=FallbackMode.CIRCUIT_BREAKER,
    max_retries=3,
    circuit_breaker_threshold=5,
    circuit_breaker_timeout_seconds=60.0
)

# Create fallback strategy
fallback_strategy = FallbackProviderStrategy(
    primary_strategy=aws_strategy,
    fallback_strategies=[provider1_strategy, provider2_strategy],
    config=fallback_config
)

# Initialize and use
fallback_strategy.initialize()
result = fallback_strategy.execute_operation(operation)
```

### **Circuit Breaker Pattern**

```python
# Circuit breaker with custom thresholds
fallback_config = FallbackConfig(
    mode=FallbackMode.CIRCUIT_BREAKER,
    circuit_breaker_threshold=10,  # Open after 10 failures
    circuit_breaker_timeout_seconds=120.0,  # Try again after 2 minutes
    enable_graceful_degradation=True
)

# Monitor circuit breaker state
metrics = fallback_strategy.circuit_metrics
print(f"Circuit state: {metrics['state']}")
print(f"Failure count: {metrics['failure_count']}")
print(f"Success rate: {metrics['failure_rate']:.1f}%")
```

### **Retry with Exponential Backoff**

```python
fallback_config = FallbackConfig(
    mode=FallbackMode.RETRY_THEN_FALLBACK,
    max_retries=5,
    retry_delay_seconds=1.0,  # Start with 1 second
    # Exponential backoff implemented internally
)
```

---

##  **Multi-Provider Composition**

### **Parallel Execution**

```python
from src.providers.base.strategy import (
    CompositeProviderStrategy,
    CompositionConfig,
    CompositionMode,
    AggregationPolicy
)

# Execute on all providers simultaneously
composite_config = CompositionConfig(
    mode=CompositionMode.PARALLEL,
    aggregation_policy=AggregationPolicy.MERGE_ALL,
    max_concurrent_operations=5,
    timeout_seconds=30.0
)

composite_strategy = CompositeProviderStrategy(
    strategies=[aws_strategy, provider1_strategy, provider2_strategy],
    config=composite_config
)

# Results from all providers will be merged
result = composite_strategy.execute_operation(operation)
```

### **Sequential Execution with First Success**

```python
composite_config = CompositionConfig(
    mode=CompositionMode.SEQUENTIAL,
    aggregation_policy=AggregationPolicy.FIRST_SUCCESS,
    require_all_success=False
)

# Will try providers in order until one succeeds
composite_strategy = CompositeProviderStrategy(
    strategies=[aws_strategy, provider1_strategy, provider2_strategy],
    config=composite_config
)
```

### **Redundant Execution for Critical Operations**

```python
composite_config = CompositionConfig(
    mode=CompositionMode.REDUNDANT,
    aggregation_policy=AggregationPolicy.MAJORITY_WINS,
    min_success_count=2,  # Need at least 2 providers to agree
    failure_threshold=0.3  # Fail if more than 30% fail
)
```

---

##  **Configuration Management**

### **Configuration File Structure**

```json
{
  "version": "2.0.0",
  "provider": {
    "type": "aws",
    "strategy": {
      "selection_policy": "performance_based",
      "health_check_interval_seconds": 60,
      "failover_enabled": true,
      "metrics_collection_enabled": true,
      "selection_criteria": {
        "min_success_rate": 95.0,
        "max_response_time_ms": 3000,
        "require_healthy": true
      }
    },
    "aws": {
      "region": "us-east-1",
      "profile": "default"
    },
    "provider1": {
      "endpoint_url": "https://api.provider1.com",
      "api_key": "${PROVIDER1_API_KEY}",
      "region": "region1"
    },
    "provider2": {
      "endpoint_url": "https://api.provider2.com",
      "api_key": "${PROVIDER2_API_KEY}",
      "region": "region2"
    },
    "load_balancing": {
      "algorithm": "adaptive",
      "health_check_mode": "hybrid",
      "weights": {
        "aws": 0.6,
        "provider1": 0.3,
        "provider2": 0.1
      }
    },
    "fallback": {
      "mode": "circuit_breaker",
      "primary": "aws",
      "fallbacks": ["provider1", "provider2"],
      "circuit_breaker_threshold": 5,
      "circuit_breaker_timeout_seconds": 60
    }
  }
}
```

### **Runtime Configuration Updates**

```python
# Update strategy selection policy at runtime
context.set_selection_policy(SelectionPolicy.LEAST_RESPONSE_TIME)

# Update selection criteria
new_criteria = SelectionCriteria(
    min_success_rate=99.0,
    max_response_time_ms=1000,
    require_healthy=True
)
context.set_selection_criteria(new_criteria)

# Update load balancing weights
load_balancer.set_strategy_weight("aws", 0.8)
load_balancer.set_strategy_weight("provider1", 0.2)
```

---

##  **Monitoring and Metrics**

### **Strategy Performance Metrics**

```python
# Get metrics for specific strategy
metrics = context.get_strategy_metrics("aws")
print(f"Total operations: {metrics.total_operations}")
print(f"Success rate: {metrics.success_rate:.1f}%")
print(f"Average response time: {metrics.average_response_time_ms:.2f}ms")

# Get metrics for all strategies
all_metrics = context.get_all_metrics()
for strategy_type, metrics in all_metrics.items():
    print(f"{strategy_type}: {metrics.success_rate:.1f}% success")
```

### **Health Monitoring**

```python
# Check health of specific strategy
health = context.check_strategy_health("aws")
print(f"AWS healthy: {health.is_healthy}")
print(f"Status: {health.status_message}")

# Monitor all strategies
for strategy_type in context.available_strategies:
    health = context.check_strategy_health(strategy_type)
    status = "[[]]" if health.is_healthy else "[[]]"
    print(f"{status} {strategy_type}: {health.status_message}")
```

### **Real-time Monitoring Dashboard**

```python
import time

def monitor_strategies(context, interval=30):
    """Monitor strategies in real-time."""
    while True:
        print("\n" + "="*50)
        print(f"Strategy Monitor - {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*50)

        for strategy_type in context.available_strategies:
            metrics = context.get_strategy_metrics(strategy_type)
            health = context.check_strategy_health(strategy_type)

            status = "HEALTHY" if health.is_healthy else "UNHEALTHY"
            print(f"{status} {strategy_type}:")
            print(f"  Operations: {metrics.total_operations}")
            print(f"  Success: {metrics.success_rate:.1f}%")
            print(f"  Response: {metrics.average_response_time_ms:.1f}ms")
            print(f"  Health: {health.status_message}")

        time.sleep(interval)

# Start monitoring
monitor_strategies(context)
```

---

## **Testing Strategies**

### **Unit Testing Individual Strategies**

```python
import pytest
from unittest.mock import Mock, patch

class TestProvider1Strategy:

    def setup_method(self):
        self.config = Provider1Config(
            endpoint_url="https://test.provider1.com",
            api_key="test-key",
            region="test-region"
        )
        self.strategy = Provider1Strategy(self.config)

    def test_provider_type(self):
        assert self.strategy.provider_type == "provider1"

    @patch('src.providers.provider1.strategy.Provider1Client')
    def test_initialization(self, mock_client):
        mock_client.return_value = Mock()
        assert self.strategy.initialize() == True
        assert self.strategy.is_initialized == True

    def test_create_instances_operation(self):
        # Mock the client and test instance creation
        with patch.object(self.strategy, '_client') as mock_client:
            mock_client.create_instances.return_value = [
                Mock(id="inst-1"), Mock(id="inst-2")
            ]

            operation = ProviderOperation(
                operation_type=ProviderOperationType.CREATE_INSTANCES,
                parameters={"template_config": {}, "count": 2}
            )

            result = self.strategy.execute_operation(operation)
            assert result.success == True
            assert len(result.data["instance_ids"]) == 2
```

### **Integration Testing with Multiple Strategies**

```python
class TestStrategyIntegration:

    def setup_method(self):
        self.context = create_provider_context()

        # Create mock strategies
        self.aws_strategy = Mock(spec=ProviderStrategy)
        self.aws_strategy.provider_type = "aws"
        self.aws_strategy.initialize.return_value = True

        self.provider1_strategy = Mock(spec=ProviderStrategy)
        self.provider1_strategy.provider_type = "provider1"
        self.provider1_strategy.initialize.return_value = True

    def test_strategy_registration(self):
        self.context.register_strategy(self.aws_strategy)
        self.context.register_strategy(self.provider1_strategy)

        assert "aws" in self.context.available_strategies
        assert "provider1" in self.context.available_strategies

    def test_strategy_switching(self):
        self.context.register_strategy(self.aws_strategy)
        self.context.register_strategy(self.provider1_strategy)
        self.context.initialize()

        # Test switching
        assert self.context.set_strategy("aws") == True
        assert self.context.current_strategy_type == "aws"

        assert self.context.set_strategy("provider1") == True
        assert self.context.current_strategy_type == "provider1"
```

### **Load Testing and Performance**

```python
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

async def load_test_strategies(context, operations_count=1000):
    """Load test strategy performance."""

    operation = ProviderOperation(
        operation_type=ProviderOperationType.HEALTH_CHECK,
        parameters={}
    )

    start_time = time.time()

    # Execute operations concurrently
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [
            executor.submit(context.execute_operation, operation)
            for _ in range(operations_count)
        ]

        results = [future.result() for future in futures]

    end_time = time.time()

    # Analyze results
    successful = sum(1 for r in results if r.success)
    failed = len(results) - successful
    duration = end_time - start_time

    print(f"Load Test Results:")
    print(f"  Operations: {operations_count}")
    print(f"  Duration: {duration:.2f}s")
    print(f"  Rate: {operations_count/duration:.1f} ops/sec")
    print(f"  Success: {successful} ({successful/len(results)*100:.1f}%)")
    print(f"  Failed: {failed}")

    return {
        "operations": operations_count,
        "duration": duration,
        "success_rate": successful/len(results),
        "ops_per_second": operations_count/duration
    }
```

---

##  **Production Deployment**

### **Environment-Specific Configuration**

```python
# config/production.json
{
  "provider": {
    "strategy": {
      "selection_policy": "performance_based",
      "health_check_interval_seconds": 30,
      "selection_criteria": {
        "min_success_rate": 99.5,
        "max_response_time_ms": 1000
      }
    },
    "load_balancing": {
      "algorithm": "adaptive",
      "health_check_mode": "active",
      "max_connections_per_strategy": 100
    },
    "fallback": {
      "mode": "circuit_breaker",
      "circuit_breaker_threshold": 3,
      "circuit_breaker_timeout_seconds": 30
    }
  }
}

# config/development.json
{
  "provider": {
    "strategy": {
      "selection_policy": "first_available",
      "health_check_interval_seconds": 60,
      "selection_criteria": {
        "min_success_rate": 80.0,
        "max_response_time_ms": 5000
      }
    }
  }
}
```

### **Deployment Checklist**

- [ ] **Strategy Configuration**: Verify all provider strategies are configured
- [ ] **Health Checks**: Ensure health check endpoints are accessible
- [ ] **Monitoring**: Set up metrics collection and alerting
- [ ] **Fallback Testing**: Test fallback scenarios in staging
- [ ] **Load Testing**: Verify performance under expected load
- [ ] **Circuit Breaker**: Test circuit breaker thresholds
- [ ] **Logging**: Ensure comprehensive logging is enabled
- [ ] **Security**: Verify API keys and credentials are secure

### **Monitoring and Alerting**

```python
# Example monitoring integration
def setup_monitoring(context):
    """Set up monitoring and alerting."""

    def check_strategy_health():
        unhealthy_strategies = []
        for strategy_type in context.available_strategies:
            health = context.check_strategy_health(strategy_type)
            if not health.is_healthy:
                unhealthy_strategies.append(strategy_type)

        if unhealthy_strategies:
            # Send alert
            send_alert(f"Unhealthy strategies: {unhealthy_strategies}")

    def check_performance_degradation():
        for strategy_type in context.available_strategies:
            metrics = context.get_strategy_metrics(strategy_type)
            if metrics.success_rate < 95.0:
                send_alert(f"{strategy_type} success rate below 95%: {metrics.success_rate:.1f}%")
            if metrics.average_response_time_ms > 2000:
                send_alert(f"{strategy_type} response time high: {metrics.average_response_time_ms:.1f}ms")

    # Schedule regular health checks
    import schedule
    schedule.every(1).minutes.do(check_strategy_health)
    schedule.every(5).minutes.do(check_performance_degradation)
```

---

##  **Troubleshooting**

### **Common Issues and Solutions**

1. **Strategy Not Found**
   ```python
   # Problem: Strategy not registered
   # Solution: Ensure strategy is registered before use
   context.register_strategy(your_strategy)
   ```

2. **Initialization Failures**
   ```python
   # Problem: Strategy fails to initialize
   # Solution: Check configuration and credentials
   if not strategy.initialize():
       health = strategy.check_health()
       print(f"Initialization failed: {health.status_message}")
   ```

3. **Circuit Breaker Stuck Open**
   ```python
   # Problem: Circuit breaker not recovering
   # Solution: Check circuit breaker timeout and health
   metrics = fallback_strategy.circuit_metrics
   if metrics['state'] == 'open':
       print(f"Circuit open since: {metrics['last_failure_time']}")
   ```

4. **Load Balancer Uneven Distribution**
   ```python
   # Problem: Requests not distributed evenly
   # Solution: Check strategy weights and health
   stats = load_balancer.strategy_stats
   for strategy, metrics in stats.items():
       print(f"{strategy}: {metrics['active_connections']} connections")
   ```

### **Debug Mode**

```python
# Enable debug logging
import logging
logging.getLogger('src.providers.base.strategy').setLevel(logging.DEBUG)

# Add debug information to operations
operation = ProviderOperation(
    operation_type=ProviderOperationType.HEALTH_CHECK,
    parameters={},
    context={"debug": True, "trace_id": "debug-123"}
)
```

---

##  **API Reference**

### **Core Classes**

- `ProviderStrategy` - Abstract base class for all provider strategies
- `ProviderContext` - Manages and executes provider strategies
- `ProviderOperation` - Represents an operation to be executed
- `ProviderResult` - Result of a provider operation
- `ProviderCapabilities` - Describes provider capabilities
- `ProviderHealthStatus` - Health status information

### **Advanced Strategies**

- `CompositeProviderStrategy` - Multi-provider composition
- `FallbackProviderStrategy` - Resilience and failover
- `LoadBalancingProviderStrategy` - Load balancing and performance

### **Configuration Classes**

- `CompositionConfig` - Configuration for composite strategies
- `FallbackConfig` - Configuration for fallback strategies
- `LoadBalancingConfig` - Configuration for load balancing

### **Enums**

- `ProviderOperationType` - Types of operations
- `SelectionPolicy` - Strategy selection policies
- `CompositionMode` - Composition execution modes
- `FallbackMode` - Fallback behavior modes
- `LoadBalancingAlgorithm` - Load balancing algorithms

---

This comprehensive guide covers all aspects of the Provider Strategy Pattern implementation. Use it as a reference for implementing new providers, configuring advanced strategies, and deploying to production environments.
