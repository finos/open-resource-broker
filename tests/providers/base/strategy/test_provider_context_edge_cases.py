"""Edge case tests for ProviderContext functionality."""

import time
from threading import Event, Thread
from unittest.mock import Mock

import pytest

from src.domain.base.ports import LoggingPort
from src.providers.base.strategy.provider_context import ProviderContext
from src.providers.base.strategy.provider_strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)


class FlakyProviderStrategy(ProviderStrategy):
    """Provider strategy that simulates flaky behavior."""

    def __init__(self, provider_type: str, failure_rate: float = 0.5):
        """Initialize the instance."""
        self._provider_type = provider_type
        self.failure_rate = failure_rate
        self._initialized = False
        self.operation_count = 0
        self.health_check_count = 0

    @property
    def provider_type(self) -> str:
        """Get provider type."""
        return self._provider_type

    def initialize(self) -> bool:
        """Initialize with potential failure."""
        if self.failure_rate > 0.8:
            return False
        self._initialized = True
        return True

    def is_initialized(self) -> bool:
        """Check if initialized."""
        return self._initialized

    def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """Execute operation with potential failure."""
        self.operation_count += 1

        # Simulate random failures
        import random

        if random.random() < self.failure_rate:
            return ProviderResult.error_result(
                f"Simulated failure #{self.operation_count}", "SIMULATED_FAILURE"
            )

        return ProviderResult.success_result(
            {"operation_count": self.operation_count, "success": True}
        )

    def get_capabilities(self) -> ProviderCapabilities:
        """Get capabilities."""
        return ProviderCapabilities(
            provider_type=self._provider_type,
            supported_operations=[ProviderOperationType.CREATE_INSTANCES],
            features={},
            limitations={},
            performance_metrics={},
        )

    def check_health(self) -> ProviderHealthStatus:
        """Check health with potential issues."""
        self.health_check_count += 1

        # Simulate intermittent health issues
        import random

        if random.random() < self.failure_rate:
            return ProviderHealthStatus.unhealthy(
                f"Health check failed #{self.health_check_count}",
                {"check_count": self.health_check_count},
            )

        return ProviderHealthStatus.healthy()

    def cleanup(self) -> None:
        """Clean up resources."""
        self._initialized = False


class SlowProviderStrategy(ProviderStrategy):
    """Provider strategy that simulates slow operations."""

    def __init__(self, provider_type: str, delay_seconds: float = 1.0):
        self._provider_type = provider_type
        self.delay_seconds = delay_seconds
        self._initialized = False

    @property
    def provider_type(self) -> str:
        """Get provider type."""
        return self._provider_type

    def initialize(self) -> bool:
        """Initialize with delay."""
        time.sleep(self.delay_seconds)
        self._initialized = True
        return True

    def is_initialized(self) -> bool:
        """Check if initialized."""
        return self._initialized

    def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """Execute operation with delay."""
        time.sleep(self.delay_seconds)
        return ProviderResult.success_result({"delayed": True})

    def get_capabilities(self) -> ProviderCapabilities:
        """Get capabilities."""
        return ProviderCapabilities(
            provider_type=self._provider_type,
            supported_operations=[ProviderOperationType.CREATE_INSTANCES],
            features={},
            limitations={},
            performance_metrics={},
        )

    def check_health(self) -> ProviderHealthStatus:
        """Check health with delay."""
        time.sleep(self.delay_seconds / 2)
        return ProviderHealthStatus.healthy()

    def cleanup(self) -> None:
        """Clean up resources."""
        self._initialized = False


class TestProviderContextEdgeCases:
    """Test edge cases for ProviderContext."""

    @pytest.fixture
    def mock_logger(self):
        """Create mock logger."""
        return Mock(spec=LoggingPort)

    @pytest.fixture
    def provider_context(self, mock_logger):
        """Create provider context instance."""
        return ProviderContext(mock_logger)

    def test_flaky_provider_behavior(self, provider_context):
        """Test handling of flaky provider behavior."""
        # Set seed for reproducible results
        import random

        random.seed(42)

        flaky_strategy = FlakyProviderStrategy("flaky", failure_rate=0.3)
        provider_context.register_strategy(flaky_strategy)
        provider_context.initialize()

        # Execute multiple operations
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 1},
        )

        results = []
        for _ in range(20):
            result = provider_context.execute_operation(operation)
            results.append(result.success)

        # Should have mix of successes and failures
        successes = sum(results)
        failures = len(results) - successes

        assert successes > 0  # Some operations should succeed
        assert failures > 0  # Some operations should fail

        # Check metrics
        metrics = provider_context.get_strategy_metrics("flaky")
        assert metrics.total_operations == 20
        assert metrics.successful_operations == successes
        assert metrics.failed_operations == failures

    def test_slow_provider_operations(self, provider_context):
        """Test handling of slow provider operations."""
        slow_strategy = SlowProviderStrategy("slow", delay_seconds=0.1)
        provider_context.register_strategy(slow_strategy)

        # Test initialization timing
        start_time = time.time()
        provider_context.initialize()
        init_time = time.time() - start_time

        assert init_time >= 0.1  # Should take at least the delay time

        # Test operation timing
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 1},
        )

        start_time = time.time()
        result = provider_context.execute_operation(operation)
        operation_time = time.time() - start_time

        assert result.success is True
        assert operation_time >= 0.1  # Should take at least the delay time

        # Check response time metrics
        metrics = provider_context.get_strategy_metrics("slow")
        assert metrics.average_response_time_ms >= 100  # At least 100ms

    def test_provider_initialization_failure_recovery(self, provider_context):
        """Test recovery from provider initialization failures."""
        # Strategy that fails initialization
        failing_strategy = FlakyProviderStrategy("failing", failure_rate=1.0)
        provider_context.register_strategy(failing_strategy)

        # Context initialization should fail
        assert provider_context.initialize() is False

        # Replace with working strategy
        working_strategy = FlakyProviderStrategy("failing", failure_rate=0.0)
        provider_context.register_strategy(working_strategy)  # Should replace

        # Now initialization should succeed
        assert provider_context.initialize() is True

    def test_concurrent_strategy_registration(self, provider_context):
        """Test concurrent strategy registration."""
        strategies_registered = []
        registration_errors = []

        def register_strategy(strategy_id):
            try:
                strategy = FlakyProviderStrategy(
                    f"concurrent-{strategy_id}", failure_rate=0.0)
                provider_context.register_strategy(strategy)
                strategies_registered.append(strategy_id)
            except Exception as e:
                registration_errors.append(e)

        # Create multiple threads registering strategies
        threads = []
        for i in range(10):
            thread = Thread(target=register_strategy, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads
        for thread in threads:
            thread.join()

        # All registrations should succeed
        assert len(registration_errors) == 0
        assert len(strategies_registered) == 10
        assert len(provider_context.available_strategies) == 10

    def test_concurrent_operation_execution(self, provider_context):
        """Test concurrent operation execution with thread safety."""
        strategy = FlakyProviderStrategy("concurrent", failure_rate=0.1)
        provider_context.register_strategy(strategy)
        provider_context.initialize()

        results = []
        errors = []

        def execute_operations():
            try:
                for _ in range(5):
                    operation = ProviderOperation(
                        operation_type=ProviderOperationType.CREATE_INSTANCES,
                        parameters={"count": 1},
                    )
                    result = provider_context.execute_operation(operation)
                    results.append(result)
            except Exception as e:
                errors.append(e)

        # Create multiple threads
        threads = []
        for _ in range(10):
            thread = Thread(target=execute_operations)
            threads.append(thread)
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        # Should have no errors and all results
        assert len(errors) == 0
        assert len(results) == 50

        # Metrics should be consistent
        metrics = provider_context.get_strategy_metrics("concurrent")
        assert metrics.total_operations == 50

    def test_strategy_unregistration_during_execution(self, provider_context):
        """Test strategy unregistration during operation execution."""
        strategy = SlowProviderStrategy("slow-unregister", delay_seconds=0.2)
        provider_context.register_strategy(strategy)
        provider_context.initialize()

        operation_completed = Event()
        unregister_completed = Event()

        def execute_slow_operation():
            operation = ProviderOperation(
                operation_type=ProviderOperationType.CREATE_INSTANCES,
                parameters={"count": 1},
            )
            result = provider_context.execute_operation(operation)
            operation_completed.set()
            return result

        def unregister_strategy():
            time.sleep(0.1)  # Wait a bit then unregister
            provider_context.unregister_strategy("slow-unregister")
            unregister_completed.set()

        # Start both operations
        op_thread = Thread(target=execute_slow_operation)
        unreg_thread = Thread(target=unregister_strategy)

        op_thread.start()
        unreg_thread.start()

        # Wait for completion
        op_thread.join()
        unreg_thread.join()

        # Both should complete
        assert operation_completed.is_set()
        assert unregister_completed.is_set()

        # Strategy should be unregistered
        assert "slow-unregister" not in provider_context.available_strategies

    def test_health_check_during_operations(self, provider_context):
        """Test health checks during ongoing operations."""
        strategy = FlakyProviderStrategy("health-check", failure_rate=0.2)
        provider_context.register_strategy(strategy)
        provider_context.initialize()

        health_results = []
        operation_results = []

        def continuous_health_checks():
            for _ in range(10):
                health = provider_context.check_strategy_health("health-check")
                health_results.append(health.is_healthy if health else False)
                time.sleep(0.01)

        def continuous_operations():
            for _ in range(10):
                operation = ProviderOperation(
                    operation_type=ProviderOperationType.CREATE_INSTANCES,
                    parameters={"count": 1},
                )
                result = provider_context.execute_operation(operation)
                operation_results.append(result.success)
                time.sleep(0.01)

        # Run both concurrently
        health_thread = Thread(target=continuous_health_checks)
        ops_thread = Thread(target=continuous_operations)

        health_thread.start()
        ops_thread.start()

        health_thread.join()
        ops_thread.join()

        # Should have results from both
        assert len(health_results) == 10
        assert len(operation_results) == 10

        # Metrics should reflect both activities
        metrics = provider_context.get_strategy_metrics("health-check")
        assert metrics.total_operations == 10
        assert metrics.health_check_count == 10

    def test_memory_usage_with_many_operations(self, provider_context):
        """Test memory usage doesn't grow unbounded with many operations."""
        import gc

        strategy = FlakyProviderStrategy("memory-test", failure_rate=0.0)
        provider_context.register_strategy(strategy)
        provider_context.initialize()

        # Get initial memory usage
        gc.collect()
        initial_objects = len(gc.get_objects())

        # Execute many operations
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 1},
        )

        for _ in range(1000):
            provider_context.execute_operation(operation)

        # Force garbage collection
        gc.collect()
        final_objects = len(gc.get_objects())

        # Memory growth should be reasonable (less than 50% increase)
        growth_ratio = final_objects / initial_objects
        assert growth_ratio < 1.5, f"Memory grew by {(growth_ratio - 1) * 100:.1f}%"

        # Metrics should be accurate
        metrics = provider_context.get_strategy_metrics("memory-test")
        assert metrics.total_operations == 1000
        assert metrics.successful_operations == 1000

    def test_strategy_replacement_edge_cases(self, provider_context):
        """Test edge cases in strategy replacement."""
        # Register initial strategy
        strategy1 = FlakyProviderStrategy("replaceable", failure_rate=0.0)
        provider_context.register_strategy(strategy1)
        provider_context.initialize()

        # Execute some operations
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 1},
        )

        for _ in range(5):
            provider_context.execute_operation(operation)

        # Get initial metrics
        initial_metrics = provider_context.get_strategy_metrics("replaceable")
        assert initial_metrics.total_operations == 5

        # Replace strategy with same name
        strategy2 = FlakyProviderStrategy("replaceable", failure_rate=0.0)
        provider_context.register_strategy(strategy2)

        # Metrics should be reset for new strategy
        new_metrics = provider_context.get_strategy_metrics("replaceable")
        assert new_metrics.total_operations == 0

        # Execute more operations
        for _ in range(3):
            provider_context.execute_operation(operation)

        # Should have new metrics
        final_metrics = provider_context.get_strategy_metrics("replaceable")
        assert final_metrics.total_operations == 3

    def test_context_manager_exception_handling(self, provider_context):
        """Test context manager behavior with exceptions."""
        strategy = FlakyProviderStrategy("exception-test", failure_rate=0.0)
        provider_context.register_strategy(strategy)

        # Test normal context manager usage
        with provider_context as ctx:
            assert ctx.is_initialized is True

        # Test context manager with exception
        try:
            with provider_context as ctx:
                assert ctx.is_initialized is True
                raise ValueError("Test exception")
        except ValueError:
            pass  # Expected

        # Context should still be properly cleaned up
        # (Note: Current implementation doesn't have explicit cleanup in __exit__)

    def test_metrics_precision_edge_cases(self, provider_context):
        """Test metrics calculation precision edge cases."""
        strategy = FlakyProviderStrategy("precision-test", failure_rate=0.0)
        provider_context.register_strategy(strategy)
        provider_context.initialize()

        # Test with very small response times
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 1},
        )

        # Mock very fast operations
        original_execute = strategy.execute_operation

        def fast_execute(op):
            time.sleep(0.001)  # 1ms
            return original_execute(op)

        strategy.execute_operation = fast_execute

        # Execute operations
        for _ in range(100):
            provider_context.execute_operation(operation)

        metrics = provider_context.get_strategy_metrics("precision-test")

        # Should handle small numbers correctly
        assert metrics.total_operations == 100
        assert metrics.successful_operations == 100
        assert metrics.success_rate == 100.0
        assert metrics.average_response_time_ms > 0  # Should be greater than 0

    def test_strategy_capabilities_edge_cases(self, provider_context):
        """Test edge cases in strategy capabilities handling."""
        # Strategy with no supported operations
        empty_strategy = Mock()
        empty_strategy.provider_type = "empty"
        empty_strategy.initialize.return_value = True
        empty_strategy.is_initialized.return_value = True
        empty_strategy.get_capabilities.return_value = ProviderCapabilities(
            supported_operations=[],  # No operations supported
            max_concurrent_operations=0,
            supports_dry_run=False,
        )

        provider_context.register_strategy(empty_strategy)
        provider_context.initialize()

        # Try to execute operation
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 1},
        )

        result = provider_context.execute_operation(operation)

        # Should fail due to unsupported operation
        assert result.success is False
        assert result.error_code == "OPERATION_NOT_SUPPORTED"
