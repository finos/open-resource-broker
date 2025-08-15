"""Tests for ProviderContext functionality."""

import time
from threading import Thread
from unittest.mock import Mock

import pytest

from domain.base.ports import LoggingPort
from providers.base.strategy.provider_context import ProviderContext, StrategyMetrics
from providers.base.strategy.provider_strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)


class MockProviderStrategy(ProviderStrategy):
    """Mock provider strategy for testing."""

    def __init__(self, provider_type: str, supports_operations=None, health_status=None):
        """Initialize the instance."""
        self._provider_type = provider_type
        self._supports_operations = supports_operations or [ProviderOperationType.CREATE_INSTANCES]
        self._health_status = health_status or ProviderHealthStatus.healthy()
        self._initialized = False
        self.execute_count = 0
        self.health_check_count = 0

    @property
    def provider_type(self) -> str:
        """Get provider type."""
        return self._provider_type

    def initialize(self) -> bool:
        """Initialize the strategy."""
        self._initialized = True
        return True

    def is_initialized(self) -> bool:
        """Check if strategy is initialized."""
        return self._initialized

    def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """Execute an operation."""
        self.execute_count += 1
        if operation.operation_type in self._supports_operations:
            return ProviderResult.success_result({"executed": True, "count": self.execute_count})
        return ProviderResult.error_result("Operation not supported", "UNSUPPORTED")

    def get_capabilities(self) -> ProviderCapabilities:
        """Get provider capabilities."""
        return ProviderCapabilities(
            provider_type=self._provider_type,
            supported_operations=self._supports_operations,
            features={},
            limitations={},
            performance_metrics={},
        )

    def check_health(self) -> ProviderHealthStatus:
        """Check provider health."""
        self.health_check_count += 1
        return self._health_status

    def cleanup(self) -> None:
        """Clean up resources."""
        self._initialized = False


class TestProviderContext:
    """Test ProviderContext functionality."""

    @pytest.fixture
    def mock_logger(self):
        """Create mock logger."""
        return Mock(spec=LoggingPort)

    @pytest.fixture
    def provider_context(self, mock_logger):
        """Create provider context instance."""
        return ProviderContext(mock_logger)

    @pytest.fixture
    def mock_strategy(self):
        """Create mock strategy."""
        return MockProviderStrategy("test-provider")

    def test_provider_context_initialization(self, provider_context, mock_logger):
        """Test provider context initialization."""
        assert not provider_context.is_initialized
        assert provider_context.current_strategy_type is None
        assert provider_context.available_strategies == []

    def test_register_strategy(self, provider_context, mock_strategy):
        """Test strategy registration."""
        provider_context.register_strategy(mock_strategy)

        assert "test-provider" in provider_context.available_strategies
        assert provider_context.current_strategy_type == "test-provider"

    def test_register_multiple_strategies(self, provider_context, mock_logger):
        """Test registering multiple strategies."""
        strategy1 = MockProviderStrategy("provider-1")
        strategy2 = MockProviderStrategy("provider-2")

        provider_context.register_strategy(strategy1)
        provider_context.register_strategy(strategy2)

        assert len(provider_context.available_strategies) == 2
        assert "provider-1" in provider_context.available_strategies
        assert "provider-2" in provider_context.available_strategies
        # First registered strategy should be current
        assert provider_context.current_strategy_type == "provider-1"

    def test_register_duplicate_strategy(self, provider_context, mock_logger):
        """Test registering duplicate strategy replaces existing."""
        strategy1 = MockProviderStrategy("same-provider")
        strategy2 = MockProviderStrategy("same-provider")

        provider_context.register_strategy(strategy1)
        provider_context.register_strategy(strategy2)

        assert len(provider_context.available_strategies) == 1
        assert provider_context.current_strategy_type == "same-provider"
        mock_logger.warning.assert_called_with(
            "Strategy same-provider already registered, replacing"
        )

    def test_register_invalid_strategy(self, provider_context):
        """Test registering invalid strategy raises error."""
        with pytest.raises(ValueError, match="Strategy must implement ProviderStrategy interface"):
            provider_context.register_strategy("not-a-strategy")

    def test_unregister_strategy(self, provider_context):
        """Test strategy unregistration."""
        strategy = MockProviderStrategy("test-provider")
        provider_context.register_strategy(strategy)

        result = provider_context.unregister_strategy("test-provider")

        assert result is True
        assert "test-provider" not in provider_context.available_strategies
        assert provider_context.current_strategy_type is None

    def test_unregister_nonexistent_strategy(self, provider_context):
        """Test unregistering nonexistent strategy."""
        result = provider_context.unregister_strategy("nonexistent")
        assert result is False

    def test_set_strategy(self, provider_context):
        """Test setting active strategy."""
        strategy1 = MockProviderStrategy("provider-1")
        strategy2 = MockProviderStrategy("provider-2")

        provider_context.register_strategy(strategy1)
        provider_context.register_strategy(strategy2)

        result = provider_context.set_strategy("provider-2")

        assert result is True
        assert provider_context.current_strategy_type == "provider-2"

    def test_set_nonexistent_strategy(self, provider_context):
        """Test setting nonexistent strategy."""
        result = provider_context.set_strategy("nonexistent")
        assert result is False

    def test_execute_operation_success(self, provider_context):
        """Test successful operation execution."""
        strategy = MockProviderStrategy("test-provider")
        provider_context.register_strategy(strategy)

        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 2},
        )

        result = provider_context.execute_operation(operation)

        assert result.success is True
        assert result.data["executed"] is True
        assert strategy.execute_count == 1

    def test_execute_operation_no_strategy(self, provider_context):
        """Test operation execution with no strategy."""
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 2},
        )

        result = provider_context.execute_operation(operation)

        assert result.success is False
        assert result.error_code == "NO_STRATEGY_AVAILABLE"

    def test_execute_operation_unsupported(self, provider_context):
        """Test operation execution with unsupported operation."""
        strategy = MockProviderStrategy(
            "test-provider",
            supports_operations=[ProviderOperationType.GET_INSTANCE_STATUS],
        )
        provider_context.register_strategy(strategy)

        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,  # Not supported
            parameters={"count": 2},
        )

        result = provider_context.execute_operation(operation)

        assert result.success is False
        assert result.error_code == "OPERATION_NOT_SUPPORTED"

    def test_execute_with_strategy(self, provider_context):
        """Test executing operation with specific strategy."""
        strategy1 = MockProviderStrategy("provider-1")
        strategy2 = MockProviderStrategy("provider-2")

        provider_context.register_strategy(strategy1)
        provider_context.register_strategy(strategy2)

        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 2},
        )

        result = provider_context.execute_with_strategy("provider-2", operation)

        assert result.success is True
        assert strategy1.execute_count == 0
        assert strategy2.execute_count == 1

    def test_execute_with_nonexistent_strategy(self, provider_context):
        """Test executing with nonexistent strategy."""
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 2},
        )

        result = provider_context.execute_with_strategy("nonexistent", operation)

        assert result.success is False
        assert result.error_code == "STRATEGY_NOT_FOUND"

    def test_check_strategy_health(self, provider_context):
        """Test strategy health checking."""
        strategy = MockProviderStrategy("test-provider")
        provider_context.register_strategy(strategy)

        health = provider_context.check_strategy_health("test-provider")

        assert health is not None
        assert health.is_healthy is True
        assert strategy.health_check_count == 1

    def test_check_current_strategy_health(self, provider_context):
        """Test current strategy health checking."""
        strategy = MockProviderStrategy("test-provider")
        provider_context.register_strategy(strategy)

        health = provider_context.check_strategy_health()

        assert health is not None
        assert health.is_healthy is True

    def test_check_nonexistent_strategy_health(self, provider_context):
        """Test health check for nonexistent strategy."""
        health = provider_context.check_strategy_health("nonexistent")
        assert health is None

    def test_get_strategy_metrics(self, provider_context):
        """Test getting strategy metrics."""
        strategy = MockProviderStrategy("test-provider")
        provider_context.register_strategy(strategy)

        # Execute some operations to generate metrics
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 2},
        )
        provider_context.execute_operation(operation)
        provider_context.execute_operation(operation)

        metrics = provider_context.get_strategy_metrics("test-provider")

        assert metrics is not None
        assert metrics.total_operations == 2
        assert metrics.successful_operations == 2
        assert metrics.success_rate == 100.0

    def test_get_all_metrics(self, provider_context):
        """Test getting all strategy metrics."""
        strategy1 = MockProviderStrategy("provider-1")
        strategy2 = MockProviderStrategy("provider-2")

        provider_context.register_strategy(strategy1)
        provider_context.register_strategy(strategy2)

        all_metrics = provider_context.get_all_metrics()

        assert len(all_metrics) == 2
        assert "provider-1" in all_metrics
        assert "provider-2" in all_metrics

    def test_get_strategy_capabilities(self, provider_context):
        """Test getting strategy capabilities."""
        strategy = MockProviderStrategy("test-provider")
        provider_context.register_strategy(strategy)

        capabilities = provider_context.get_strategy_capabilities("test-provider")

        assert capabilities is not None
        assert ProviderOperationType.CREATE_INSTANCES in capabilities.supported_operations

    def test_context_manager(self, provider_context):
        """Test provider context as context manager."""
        strategy = MockProviderStrategy("test-provider")
        provider_context.register_strategy(strategy)

        with provider_context as ctx:
            assert ctx is provider_context
            assert ctx.is_initialized is True

    def test_context_manager_initialization_failure(self, provider_context):
        """Test context manager with initialization failure."""
        # Don't register any strategies to cause initialization failure
        with pytest.raises(RuntimeError, match="Failed to initialize provider context"):
            with provider_context:
                pass

    def test_initialize_context(self, provider_context):
        """Test context initialization."""
        strategy = MockProviderStrategy("test-provider")
        provider_context.register_strategy(strategy)

        result = provider_context.initialize()

        assert result is True
        assert provider_context.is_initialized is True
        assert strategy.is_initialized() is True

    def test_initialize_context_no_strategies(self, provider_context):
        """Test context initialization with no strategies."""
        result = provider_context.initialize()
        assert result is False

    def test_strategy_metrics_recording(self, provider_context):
        """Test that strategy metrics are recorded correctly."""
        strategy = MockProviderStrategy(
            "test-provider",
            supports_operations=[ProviderOperationType.CREATE_INSTANCES],
        )
        provider_context.register_strategy(strategy)

        # Execute successful operations
        start_time = time.time()
        success_operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 2},
        )
        provider_context.execute_operation(success_operation)
        provider_context.execute_operation(success_operation)

        # Execute failed operation (unsupported) - this will be caught by capability check
        failed_operation = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,  # Not supported
            parameters={"count": 1},
        )
        provider_context.execute_operation(failed_operation)

        metrics = provider_context.get_strategy_metrics("test-provider")

        # Only 3 operations total (2 successful + 1 failed due to unsupported operation)
        assert metrics.total_operations == 3
        assert metrics.successful_operations == 2
        assert metrics.failed_operations == 1
        assert metrics.success_rate == pytest.approx(66.67, rel=1e-2)
        assert metrics.last_used_time >= start_time

    def test_concurrent_strategy_access(self, provider_context):
        """Test concurrent access to strategies."""
        strategy = MockProviderStrategy("test-provider")
        provider_context.register_strategy(strategy)

        results = []

        def execute_operations():
            for _ in range(10):
                operation = ProviderOperation(
                    operation_type=ProviderOperationType.CREATE_INSTANCES,
                    parameters={"count": 1},
                )
                result = provider_context.execute_operation(operation)
                results.append(result.success)

        # Create multiple threads
        threads = [Thread(target=execute_operations) for _ in range(5)]

        # Start all threads
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify all operations succeeded
        assert all(results)
        assert len(results) == 50

        # Verify metrics
        metrics = provider_context.get_strategy_metrics("test-provider")
        assert metrics.total_operations == 50
        assert metrics.successful_operations == 50

    def test_strategy_health_check_metrics(self, provider_context):
        """Test health check metrics recording."""
        strategy = MockProviderStrategy("test-provider")
        provider_context.register_strategy(strategy)

        # Perform health checks
        provider_context.check_strategy_health("test-provider")
        provider_context.check_strategy_health("test-provider")
        provider_context.check_strategy_health("test-provider")

        metrics = provider_context.get_strategy_metrics("test-provider")

        assert metrics.health_check_count == 3
        assert metrics.last_health_check is not None

    def test_strategy_cleanup_on_unregister(self, provider_context):
        """Test strategy cleanup when unregistered."""
        strategy = MockProviderStrategy("test-provider")
        provider_context.register_strategy(strategy)

        # Initialize strategy
        provider_context.initialize()
        assert strategy.is_initialized() is True

        # Unregister strategy
        provider_context.unregister_strategy("test-provider")

        # Strategy should be cleaned up
        assert strategy.is_initialized() is False


class TestStrategyMetrics:
    """Test StrategyMetrics functionality."""

    def test_metrics_initialization(self):
        """Test metrics initialization."""
        metrics = StrategyMetrics()

        assert metrics.total_operations == 0
        assert metrics.successful_operations == 0
        assert metrics.failed_operations == 0
        assert metrics.success_rate == 0.0
        assert metrics.average_response_time_ms == 0.0
        assert metrics.last_used_time is None
        assert metrics.health_check_count == 0
        assert metrics.last_health_check is None

    def test_record_successful_operation(self):
        """Test recording successful operation."""
        metrics = StrategyMetrics()

        metrics.record_operation(True, 100.0)

        assert metrics.total_operations == 1
        assert metrics.successful_operations == 1
        assert metrics.failed_operations == 0
        assert metrics.success_rate == 100.0
        assert metrics.average_response_time_ms == 100.0

    def test_record_failed_operation(self):
        """Test recording failed operation."""
        metrics = StrategyMetrics()

        metrics.record_operation(False, 50.0)

        assert metrics.total_operations == 1
        assert metrics.successful_operations == 0
        assert metrics.failed_operations == 1
        assert metrics.success_rate == 0.0
        assert metrics.average_response_time_ms == 50.0

    def test_record_multiple_operations(self):
        """Test recording multiple operations."""
        metrics = StrategyMetrics()

        metrics.record_operation(True, 100.0)
        metrics.record_operation(True, 200.0)
        metrics.record_operation(False, 50.0)

        assert metrics.total_operations == 3
        assert metrics.successful_operations == 2
        assert metrics.failed_operations == 1
        assert metrics.success_rate == pytest.approx(66.67, rel=1e-2)
        assert metrics.average_response_time_ms == pytest.approx(116.67, rel=1e-2)

    def test_success_rate_calculation(self):
        """Test success rate calculation edge cases."""
        metrics = StrategyMetrics()

        # No operations
        assert metrics.success_rate == 0.0

        # All successful
        metrics.record_operation(True, 100.0)
        metrics.record_operation(True, 100.0)
        assert metrics.success_rate == 100.0

        # All failed
        metrics = StrategyMetrics()
        metrics.record_operation(False, 100.0)
        metrics.record_operation(False, 100.0)
        assert metrics.success_rate == 0.0
