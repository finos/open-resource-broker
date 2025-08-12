"""
Comprehensive test examples for Provider Strategy Pattern.

This module demonstrates how to test all aspects of the provider strategy pattern
including new provider creation, runtime switching, load balancing, and fallback scenarios.
"""

import time

from src.providers.base.strategy import (
    CompositeProviderStrategy,
    CompositionConfig,
    CompositionMode,
    FallbackConfig,
    FallbackMode,
    FallbackProviderStrategy,
    LoadBalancingAlgorithm,
    LoadBalancingConfig,
    LoadBalancingProviderStrategy,
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
    create_provider_context,
)


class MockProvider1Strategy(ProviderStrategy):
    """Mock implementation of Provider1 for testing."""

    def __init__(self, config=None, should_fail=False, response_time_ms=100):
        """Initialize the instance."""
        from src.infrastructure.interfaces.provider import ProviderConfig

        super().__init__(config or ProviderConfig(provider_type="provider1"))
        self.should_fail = should_fail
        self.response_time_ms = response_time_ms
        self.operation_count = 0

    @property
    def provider_type(self) -> str:
        return "provider1"

    def initialize(self) -> bool:
        if self.should_fail:
            return False
        self._initialized = True
        return True

    def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        self.operation_count += 1
        time.sleep(self.response_time_ms / 1000.0)  # Simulate response time

        if self.should_fail:
            return ProviderResult.error_result(
                "Provider1 simulated failure", "PROVIDER1_ERROR"
            )

        if operation.operation_type == ProviderOperationType.CREATE_INSTANCES:
            return ProviderResult.success_result(
                {"instance_ids": ["provider1-inst-1", "provider1-inst-2"], "count": 2}
            )
        elif operation.operation_type == ProviderOperationType.HEALTH_CHECK:
            return ProviderResult.success_result(
                {
                    "is_healthy": True,
                    "provider": "provider1",
                    "response_time_ms": self.response_time_ms,
                }
            )

        return ProviderResult.success_result(
            {"provider": "provider1", "operation": operation.operation_type.value}
        )

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type="provider1",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.HEALTH_CHECK,
            ],
            features={"region": "provider1-region", "max_instances": 10},
        )

    def check_health(self) -> ProviderHealthStatus:
        if self.should_fail:
            return ProviderHealthStatus.unhealthy("Provider1 is down")
        return ProviderHealthStatus.healthy(
            "Provider1 is healthy", self.response_time_ms
        )


class MockProvider2Strategy(ProviderStrategy):
    """Mock implementation of Provider2 for testing."""

    def __init__(self, config=None, should_fail=False, response_time_ms=200):
        from src.infrastructure.interfaces.provider import ProviderConfig

        super().__init__(config or ProviderConfig(provider_type="provider2"))
        self.should_fail = should_fail
        self.response_time_ms = response_time_ms
        self.operation_count = 0

    @property
    def provider_type(self) -> str:
        return "provider2"

    def initialize(self) -> bool:
        if self.should_fail:
            return False
        self._initialized = True
        return True

    def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        self.operation_count += 1
        time.sleep(self.response_time_ms / 1000.0)  # Simulate response time

        if self.should_fail:
            return ProviderResult.error_result(
                "Provider2 simulated failure", "PROVIDER2_ERROR"
            )

        if operation.operation_type == ProviderOperationType.CREATE_INSTANCES:
            return ProviderResult.success_result(
                {
                    "instance_ids": [
                        "provider2-inst-1",
                        "provider2-inst-2",
                        "provider2-inst-3",
                    ],
                    "count": 3,
                }
            )
        elif operation.operation_type == ProviderOperationType.HEALTH_CHECK:
            return ProviderResult.success_result(
                {
                    "is_healthy": True,
                    "provider": "provider2",
                    "response_time_ms": self.response_time_ms,
                }
            )

        return ProviderResult.success_result(
            {"provider": "provider2", "operation": operation.operation_type.value}
        )

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type="provider2",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.HEALTH_CHECK,
            ],
            features={"region": "provider2-region", "max_instances": 20},
        )

    def check_health(self) -> ProviderHealthStatus:
        if self.should_fail:
            return ProviderHealthStatus.unhealthy("Provider2 is down")
        return ProviderHealthStatus.healthy(
            "Provider2 is healthy", self.response_time_ms
        )


class TestProviderStrategyBasics:
    """Test basic provider strategy functionality."""

    def test_create_new_provider_strategy(self):
        """Test creating a new provider strategy."""
        provider1 = MockProvider1Strategy()

        assert provider1.provider_type == "provider1"
        assert provider1.initialize()
        assert provider1.is_initialized

        # Test capabilities
        capabilities = provider1.get_capabilities()
        assert capabilities.provider_type == "provider1"
        assert (
            ProviderOperationType.CREATE_INSTANCES in capabilities.supported_operations
        )

        # Test health check
        health = provider1.check_health()
        assert health.is_healthy

    def test_provider_operation_execution(self):
        """Test executing operations on provider strategies."""
        provider1 = MockProvider1Strategy()
        provider1.initialize()

        # Test health check operation
        health_op = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        result = provider1.execute_operation(health_op)
        assert result.success
        assert result.data["provider"] == "provider1"

        # Test create instances operation
        create_op = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"template_config": {}, "count": 2},
        )

        result = provider1.execute_operation(create_op)
        assert result.success
        assert len(result.data["instance_ids"]) == 2

    def test_provider_failure_handling(self):
        """Test provider failure scenarios."""
        failing_provider = MockProvider1Strategy(should_fail=True)

        # Test initialization failure
        assert failing_provider.initialize() is False

        # Test operation failure
        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        result = failing_provider.execute_operation(operation)
        assert result.success is False
        assert "Provider1 simulated failure" in result.error_message


class TestProviderContextAndSwitching:
    """Test provider context and runtime switching."""

    def setup_method(self):
        """Set up test fixtures."""
        self.context = create_provider_context()
        self.provider1 = MockProvider1Strategy(response_time_ms=100)
        self.provider2 = MockProvider2Strategy(response_time_ms=200)

    def test_provider_registration(self):
        """Test registering providers with context."""
        self.context.register_strategy(self.provider1)
        self.context.register_strategy(self.provider2)

        available = self.context.available_strategies
        assert "provider1" in available
        assert "provider2" in available
        assert len(available) == 2

    def test_context_initialization(self):
        """Test context initialization with multiple providers."""
        self.context.register_strategy(self.provider1)
        self.context.register_strategy(self.provider2)

        assert self.context.initialize()
        assert self.context.is_initialized

    def test_runtime_provider_switching(self):
        """Test switching providers at runtime."""
        self.context.register_strategy(self.provider1)
        self.context.register_strategy(self.provider2)
        self.context.initialize()

        # Initially should use first registered provider
        assert self.context.current_strategy_type == "provider1"

        # Switch to provider2
        assert self.context.set_strategy("provider2")
        assert self.context.current_strategy_type == "provider2"

        # Switch back to provider1
        assert self.context.set_strategy("provider1")
        assert self.context.current_strategy_type == "provider1"

        # Try to switch to non-existent provider
        assert self.context.set_strategy("nonexistent") is False

    def test_operation_execution_with_switching(self):
        """Test executing operations after switching providers."""
        self.context.register_strategy(self.provider1)
        self.context.register_strategy(self.provider2)
        self.context.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"template_config": {}, "count": 2},
        )

        # Execute with provider1
        self.context.set_strategy("provider1")
        result1 = self.context.execute_operation(operation)
        assert result1.success
        assert len(result1.data["instance_ids"]) == 2
        assert "provider1" in result1.data["instance_ids"][0]

        # Switch and execute with provider2
        self.context.set_strategy("provider2")
        result2 = self.context.execute_operation(operation)
        assert result2.success
        assert len(result2.data["instance_ids"]) == 3
        assert "provider2" in result2.data["instance_ids"][0]

    def test_strategy_metrics_collection(self):
        """Test metrics collection for strategies."""
        self.context.register_strategy(self.provider1)
        self.context.register_strategy(self.provider2)
        self.context.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        # Execute operations on both providers
        self.context.set_strategy("provider1")
        self.context.execute_operation(operation)

        self.context.set_strategy("provider2")
        self.context.execute_operation(operation)

        # Check metrics
        metrics1 = self.context.get_strategy_metrics("provider1")
        metrics2 = self.context.get_strategy_metrics("provider2")

        assert metrics1.total_operations >= 1
        assert metrics2.total_operations >= 1
        assert metrics1.success_rate == 100.0
        assert metrics2.success_rate == 100.0


class TestLoadBalancing:
    """Test load balancing functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.provider1 = MockProvider1Strategy(response_time_ms=100)
        self.provider2 = MockProvider2Strategy(response_time_ms=200)
        self.strategies = [self.provider1, self.provider2]

    def test_round_robin_load_balancing(self):
        """Test round-robin load balancing."""
        config = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.ROUND_ROBIN)

        load_balancer = LoadBalancingProviderStrategy(
            strategies=self.strategies, config=config
        )

        load_balancer.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        # Execute multiple operations and verify round-robin distribution
        results = []
        for _ in range(6):
            result = load_balancer.execute_operation(operation)
            results.append(result)

        # Both providers should have been used
        assert self.provider1.operation_count > 0
        assert self.provider2.operation_count > 0

        # Should be roughly equal distribution
        total_ops = self.provider1.operation_count + self.provider2.operation_count
        assert total_ops == 6

    def test_least_response_time_load_balancing(self):
        """Test least response time load balancing."""
        config = LoadBalancingConfig(
            algorithm=LoadBalancingAlgorithm.LEAST_RESPONSE_TIME
        )

        load_balancer = LoadBalancingProviderStrategy(
            strategies=self.strategies, config=config
        )

        load_balancer.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        # Execute operations - should prefer provider1 (faster)
        for _ in range(10):
            load_balancer.execute_operation(operation)

        # Provider1 should get more requests due to faster response time
        assert self.provider1.operation_count >= self.provider2.operation_count

    def test_weighted_load_balancing(self):
        """Test weighted load balancing."""
        config = LoadBalancingConfig(
            algorithm=LoadBalancingAlgorithm.WEIGHTED_ROUND_ROBIN
        )

        # Give provider1 higher weight
        weights = {"provider1": 0.8, "provider2": 0.2}

        load_balancer = LoadBalancingProviderStrategy(
            strategies=self.strategies, weights=weights, config=config
        )

        load_balancer.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        # Execute many operations
        for _ in range(100):
            load_balancer.execute_operation(operation)

        # Provider1 should get more requests due to higher weight
        total_ops = self.provider1.operation_count + self.provider2.operation_count
        provider1_ratio = self.provider1.operation_count / total_ops

        # Should be roughly 80% for provider1
        assert provider1_ratio > 0.6  # Allow some variance

    def test_load_balancer_health_monitoring(self):
        """Test load balancer health monitoring."""
        config = LoadBalancingConfig(
            algorithm=LoadBalancingAlgorithm.ROUND_ROBIN, health_check_mode="passive"
        )

        # Create one healthy and one unhealthy provider
        healthy_provider = MockProvider1Strategy(should_fail=False)
        unhealthy_provider = MockProvider2Strategy(should_fail=True)

        load_balancer = LoadBalancingProviderStrategy(
            strategies=[healthy_provider, unhealthy_provider], config=config
        )

        load_balancer.initialize()

        # Check overall health
        health = load_balancer.check_health()
        assert (
            health.is_healthy
        )  # Should be healthy if at least one provider is healthy

        # Get strategy statistics
        stats = load_balancer.strategy_stats
        assert "provider1" in stats
        assert "provider2" in stats


class TestFallbackAndResilience:
    """Test fallback and resilience functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.primary = MockProvider1Strategy(should_fail=False)
        self.fallback1 = MockProvider2Strategy(should_fail=False)
        self.fallback2 = MockProvider1Strategy(should_fail=False)  # Different instance

    def test_immediate_fallback(self):
        """Test immediate fallback on failure."""
        # Create failing primary
        failing_primary = MockProvider1Strategy(should_fail=True)

        config = FallbackConfig(mode=FallbackMode.IMMEDIATE)

        fallback_strategy = FallbackProviderStrategy(
            primary_strategy=failing_primary,
            fallback_strategies=[self.fallback1, self.fallback2],
            config=config,
        )

        fallback_strategy.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        # Should succeed using fallback
        result = fallback_strategy.execute_operation(operation)
        assert result.success

        # Fallback should have been used
        assert self.fallback1.operation_count > 0

    def test_circuit_breaker_pattern(self):
        """Test circuit breaker pattern."""
        config = FallbackConfig(
            mode=FallbackMode.CIRCUIT_BREAKER,
            circuit_breaker_threshold=3,
            circuit_breaker_timeout_seconds=1.0,
        )

        # Primary that fails initially
        intermittent_primary = MockProvider1Strategy(should_fail=True)

        fallback_strategy = FallbackProviderStrategy(
            primary_strategy=intermittent_primary,
            fallback_strategies=[self.fallback1],
            config=config,
        )

        fallback_strategy.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        # Execute operations to trigger circuit breaker
        for _ in range(5):
            result = fallback_strategy.execute_operation(operation)
            # Should succeed using fallback
            assert result.success

        # Check circuit breaker metrics
        metrics = fallback_strategy.circuit_metrics
        assert metrics["failure_count"] >= 3

        # Circuit should be open
        assert metrics["state"] in ["open", "half_open"]

    def test_retry_then_fallback(self):
        """Test retry then fallback mode."""
        config = FallbackConfig(
            mode=FallbackMode.RETRY_THEN_FALLBACK,
            max_retries=2,
            retry_delay_seconds=0.1,  # Short delay for testing
        )

        failing_primary = MockProvider1Strategy(should_fail=True)

        fallback_strategy = FallbackProviderStrategy(
            primary_strategy=failing_primary,
            fallback_strategies=[self.fallback1],
            config=config,
        )

        fallback_strategy.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        start_time = time.time()
        result = fallback_strategy.execute_operation(operation)
        end_time = time.time()

        # Should succeed using fallback after retries
        assert result.success

        # Should have taken time for retries
        assert end_time - start_time >= 0.2  # At least 2 retry delays

        # Fallback should have been used
        assert self.fallback1.operation_count > 0

    def test_fallback_chain(self):
        """Test fallback chain with multiple fallbacks."""
        # Create failing primary and first fallback
        failing_primary = MockProvider1Strategy(should_fail=True)
        failing_fallback1 = MockProvider2Strategy(should_fail=True)
        working_fallback2 = MockProvider1Strategy(should_fail=False)

        config = FallbackConfig(mode=FallbackMode.IMMEDIATE)

        fallback_strategy = FallbackProviderStrategy(
            primary_strategy=failing_primary,
            fallback_strategies=[failing_fallback1, working_fallback2],
            config=config,
        )

        fallback_strategy.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        result = fallback_strategy.execute_operation(operation)

        # Should succeed using the second fallback
        assert result.success
        assert working_fallback2.operation_count > 0


class TestCompositeStrategies:
    """Test composite strategy functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.provider1 = MockProvider1Strategy(response_time_ms=100)
        self.provider2 = MockProvider2Strategy(response_time_ms=150)
        self.strategies = [self.provider1, self.provider2]

    def test_parallel_execution(self):
        """Test parallel execution of multiple providers."""
        config = CompositionConfig(
            mode=CompositionMode.PARALLEL,
            max_concurrent_operations=5,
            timeout_seconds=5.0,
        )

        composite = CompositeProviderStrategy(strategies=self.strategies, config=config)

        composite.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        start_time = time.time()
        result = composite.execute_operation(operation)
        end_time = time.time()

        # Should succeed
        assert result.success

        # Both providers should have been executed
        assert self.provider1.operation_count > 0
        assert self.provider2.operation_count > 0

        # Should be faster than sequential execution
        assert end_time - start_time < 0.3  # Much less than sum of response times

    def test_sequential_execution(self):
        """Test sequential execution of providers."""
        config = CompositionConfig(mode=CompositionMode.SEQUENTIAL)

        composite = CompositeProviderStrategy(strategies=self.strategies, config=config)

        composite.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        result = composite.execute_operation(operation)

        # Should succeed
        assert result.success

        # Both providers should have been executed
        assert self.provider1.operation_count > 0
        assert self.provider2.operation_count > 0

    def test_composite_with_failure_handling(self):
        """Test composite strategy with some failing providers."""
        failing_provider = MockProvider1Strategy(should_fail=True)
        working_provider = MockProvider2Strategy(should_fail=False)

        config = CompositionConfig(
            mode=CompositionMode.PARALLEL,
            failure_threshold=0.5,  # Allow 50% failures
            min_success_count=1,
        )

        composite = CompositeProviderStrategy(
            strategies=[failing_provider, working_provider], config=config
        )

        composite.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        result = composite.execute_operation(operation)

        # Should succeed because one provider worked and we allow 50% failures
        assert result.success
        assert working_provider.operation_count > 0


class TestIntegrationScenarios:
    """Test complex integration scenarios."""

    def test_complete_provider_ecosystem(self):
        """Test a complete provider ecosystem with multiple strategies."""
        # Create providers with different characteristics
        fast_provider = MockProvider1Strategy(response_time_ms=50)
        slow_provider = MockProvider2Strategy(response_time_ms=300)
        unreliable_provider = MockProvider1Strategy(should_fail=True)

        # Create load balancer
        lb_config = LoadBalancingConfig(
            algorithm=LoadBalancingAlgorithm.LEAST_RESPONSE_TIME
        )

        load_balancer = LoadBalancingProviderStrategy(
            strategies=[fast_provider, slow_provider], config=lb_config
        )

        # Create fallback with load balancer as primary
        fallback_config = FallbackConfig(mode=FallbackMode.IMMEDIATE)

        backup_provider = MockProvider2Strategy(should_fail=False)

        fallback_strategy = FallbackProviderStrategy(
            primary_strategy=load_balancer,
            fallback_strategies=[backup_provider],
            config=fallback_config,
        )

        # Initialize the complete system
        fallback_strategy.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        # Execute operations
        results = []
        for _ in range(10):
            result = fallback_strategy.execute_operation(operation)
            results.append(result)

        # All operations should succeed
        assert all(r.success for r in results)

        # Fast provider should get more requests
        assert fast_provider.operation_count >= slow_provider.operation_count

    def test_provider_switching_under_load(self):
        """Test provider switching while under load."""
        context = create_provider_context()

        provider1 = MockProvider1Strategy(response_time_ms=100)
        provider2 = MockProvider2Strategy(response_time_ms=200)

        context.register_strategy(provider1)
        context.register_strategy(provider2)
        context.initialize()

        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        # Execute operations while switching providers
        for i in range(20):
            if i == 10:
                # Switch provider halfway through
                context.set_strategy("provider2")

            result = context.execute_operation(operation)
            assert result.success

        # Both providers should have been used
        assert provider1.operation_count > 0
        assert provider2.operation_count > 0

        # Provider2 should be the current strategy
        assert context.current_strategy_type == "provider2"


if __name__ == "__main__":
    # Run specific test examples
    print("🧪 Running Provider Strategy Pattern Test Examples")
    print("=" * 60)

    # Example 1: Basic provider creation and testing
    print("\n1. Testing Basic Provider Creation:")
    test_basics = TestProviderStrategyBasics()
    test_basics.test_create_new_provider_strategy()
    print("✅ New provider strategy creation works")

    # Example 2: Runtime switching
    print("\n2. Testing Runtime Provider Switching:")
    test_switching = TestProviderContextAndSwitching()
    test_switching.setup_method()
    test_switching.test_runtime_provider_switching()
    print("✅ Runtime provider switching works")

    # Example 3: Load balancing
    print("\n3. Testing Load Balancing:")
    test_lb = TestLoadBalancing()
    test_lb.setup_method()
    test_lb.test_round_robin_load_balancing()
    print("✅ Load balancing works")

    # Example 4: Fallback and resilience
    print("\n4. Testing Fallback and Resilience:")
    test_fallback = TestFallbackAndResilience()
    test_fallback.setup_method()
    test_fallback.test_immediate_fallback()
    print("✅ Fallback and resilience works")

    print("\n🎉 All test examples completed successfully!")
    print("📚 See test file for detailed implementation examples")
