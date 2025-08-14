"""Integration tests for ProviderContext with real provider strategies."""

from unittest.mock import Mock, patch

import pytest

from src.domain.base.ports import LoggingPort
from src.providers.base.strategy.provider_context import ProviderContext
from src.providers.base.strategy.provider_strategy import (
    ProviderOperation,
    ProviderOperationType,
)


class TestProviderContextIntegration:
    """Integration tests for ProviderContext."""

    @pytest.fixture
    def mock_logger(self):
        """Create mock logger."""
        return Mock(spec=LoggingPort)

    @pytest.fixture
    def provider_context(self, mock_logger):
        """Create provider context instance."""
        return ProviderContext(mock_logger)

    def test_provider_context_with_aws_strategy(self, provider_context):
        """Test provider context with AWS strategy integration."""
        # Mock AWS provider strategy
        with patch(
            "src.providers.aws.strategy.aws_provider_strategy.AWSProviderStrategy"
        ) as MockAWSStrategy:
            mock_aws_strategy = Mock()
            mock_aws_strategy.provider_type = "aws"
            mock_aws_strategy.initialize.return_value = True
            mock_aws_strategy.is_initialized.return_value = True

            # Mock capabilities
            from src.providers.base.strategy.provider_strategy import (
                ProviderCapabilities,
            )

            mock_capabilities = ProviderCapabilities(
                supported_operations=[
                    ProviderOperationType.CREATE_INSTANCES,
                    ProviderOperationType.TERMINATE_INSTANCES,
                    ProviderOperationType.GET_INSTANCE_STATUS,
                ],
                provider_type="test",
                features={},
                limitations={},
                performance_metrics={},
            )
            mock_aws_strategy.get_capabilities.return_value = mock_capabilities

            MockAWSStrategy.return_value = mock_aws_strategy

            # Register strategy
            provider_context.register_strategy(mock_aws_strategy)

            # Test initialization
            assert provider_context.initialize() is True
            assert provider_context.current_strategy_type == "aws"

            # Test capabilities
            capabilities = provider_context.get_strategy_capabilities("aws")
            assert capabilities is not None
            assert ProviderOperationType.CREATE_INSTANCES in capabilities.supported_operations

    def test_multi_provider_context_scenario(self, provider_context):
        """Test multi-provider context scenario."""
        # Create mock strategies for different providers
        aws_strategy = Mock()
        aws_strategy.provider_type = "aws"
        aws_strategy.initialize.return_value = True
        aws_strategy.is_initialized.return_value = True

        provider1_strategy = Mock()
        provider1_strategy.provider_type = "provider1"
        provider1_strategy.initialize.return_value = True
        provider1_strategy.is_initialized.return_value = True

        # Register both strategies
        provider_context.register_strategy(aws_strategy)
        provider_context.register_strategy(provider1_strategy)

        # Test that both are available
        assert len(provider_context.available_strategies) == 2
        assert "aws" in provider_context.available_strategies
        assert "provider1" in provider_context.available_strategies

        # Test switching between strategies
        assert provider_context.set_strategy("provider1") is True
        assert provider_context.current_strategy_type == "provider1"

        assert provider_context.set_strategy("aws") is True
        assert provider_context.current_strategy_type == "aws"

    def test_provider_context_error_handling(self, provider_context):
        """Test provider context error handling scenarios."""
        # Test with strategy that fails initialization
        failing_strategy = Mock()
        failing_strategy.provider_type = "failing"
        failing_strategy.initialize.return_value = False
        failing_strategy.is_initialized.return_value = False

        provider_context.register_strategy(failing_strategy)

        # Context initialization should fail
        assert provider_context.initialize() is False

    def test_provider_context_with_health_monitoring(self, provider_context):
        """Test provider context with health monitoring."""
        from src.providers.base.strategy.provider_strategy import ProviderHealthStatus

        # Create strategy with health status
        strategy = Mock()
        strategy.provider_type = "monitored"
        strategy.initialize.return_value = True
        strategy.is_initialized.return_value = True
        strategy.check_health.return_value = ProviderHealthStatus.healthy()

        provider_context.register_strategy(strategy)
        provider_context.initialize()

        # Test health monitoring
        health = provider_context.check_strategy_health("monitored")
        assert health is not None
        assert health.is_healthy is True

        # Test metrics tracking
        metrics = provider_context.get_strategy_metrics("monitored")
        assert metrics is not None
        assert metrics.health_check_count == 1

    def test_provider_context_operation_routing(self, provider_context):
        """Test operation routing to specific providers."""
        from src.providers.base.strategy.provider_strategy import (
            ProviderCapabilities,
            ProviderResult,
        )

        # Create strategies with different capabilities
        compute_strategy = Mock()
        compute_strategy.provider_type = "compute"
        compute_strategy.initialize.return_value = True
        compute_strategy.is_initialized.return_value = True
        compute_strategy.get_capabilities.return_value = ProviderCapabilities(
            supported_operations=[ProviderOperationType.CREATE_INSTANCES],
            provider_type="test",
            features={},
            limitations={},
            performance_metrics={},
        )
        compute_strategy.execute_operation.return_value = ProviderResult.success_result(
            {"provider": "compute"}
        )

        storage_strategy = Mock()
        storage_strategy.provider_type = "storage"
        storage_strategy.initialize.return_value = True
        storage_strategy.is_initialized.return_value = True
        storage_strategy.get_capabilities.return_value = ProviderCapabilities(
            supported_operations=[ProviderOperationType.GET_INSTANCE_STATUS],
            provider_type="test",
            features={},
            limitations={},
            performance_metrics={},
        )
        storage_strategy.execute_operation.return_value = ProviderResult.success_result(
            {"provider": "storage"}
        )

        provider_context.register_strategy(compute_strategy)
        provider_context.register_strategy(storage_strategy)
        provider_context.initialize()

        # Test routing to specific provider
        launch_operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 2},
        )

        result = provider_context.execute_with_strategy("compute", launch_operation)
        assert result.success is True
        assert result.data["provider"] == "compute"

        status_operation = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={"instance_id": "i-123"},
        )

        result = provider_context.execute_with_strategy("storage", status_operation)
        assert result.success is True
        assert result.data["provider"] == "storage"

    def test_provider_context_metrics_aggregation(self, provider_context):
        """Test metrics aggregation across multiple providers."""
        from src.providers.base.strategy.provider_strategy import (
            ProviderCapabilities,
            ProviderResult,
        )

        # Create multiple strategies
        strategies = []
        for i in range(3):
            strategy = Mock()
            strategy.provider_type = f"provider-{i}"
            strategy.initialize.return_value = True
            strategy.is_initialized.return_value = True
            strategy.get_capabilities.return_value = ProviderCapabilities(
                supported_operations=[ProviderOperationType.CREATE_INSTANCES],
                provider_type="test",
                features={},
                limitations={},
                performance_metrics={},
            )
            strategy.execute_operation.return_value = ProviderResult.success_result({
                                                                                    "provider": i})
            strategies.append(strategy)
            provider_context.register_strategy(strategy)

        provider_context.initialize()

        # Execute operations on different providers
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 1},
        )

        for i, _strategy in enumerate(strategies):
            for _ in range(i + 1):  # Execute 1, 2, 3 operations respectively
                provider_context.execute_with_strategy(f"provider-{i}", operation)

        # Test metrics aggregation
        all_metrics = provider_context.get_all_metrics()
        assert len(all_metrics) == 3

        for i in range(3):
            metrics = all_metrics[f"provider-{i}"]
            assert metrics.total_operations == i + 1
            assert metrics.successful_operations == i + 1
            assert metrics.success_rate == 100.0

    def test_provider_context_concurrent_operations(self, provider_context):
        """Test concurrent operations across multiple providers."""
        import threading

        from src.providers.base.strategy.provider_strategy import (
            ProviderCapabilities,
            ProviderResult,
        )

        # Create thread-safe strategy
        strategy = Mock()
        strategy.provider_type = "concurrent"
        strategy.initialize.return_value = True
        strategy.is_initialized.return_value = True
        strategy.get_capabilities.return_value = ProviderCapabilities(
            supported_operations=[ProviderOperationType.CREATE_INSTANCES],
            provider_type="test",
            features={},
            limitations={},
            performance_metrics={},
        )

        # Thread-safe operation counter
        operation_count = threading.local()
        operation_count.value = 0

        def execute_operation_mock(operation):
            operation_count.value += 1
            return ProviderResult.success_result({"count": operation_count.value})

        strategy.execute_operation.side_effect = execute_operation_mock

        provider_context.register_strategy(strategy)
        provider_context.initialize()

        # Execute concurrent operations
        results = []
        threads = []

        def execute_operations():
            for _ in range(10):
                operation = ProviderOperation(
                    operation_type=ProviderOperationType.CREATE_INSTANCES,
                    parameters={"count": 1},
                )
                result = provider_context.execute_operation(operation)
                results.append(result)

        # Create and start threads
        for _ in range(5):
            thread = threading.Thread(target=execute_operations)
            threads.append(thread)
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        # Verify results
        assert len(results) == 50
        assert all(result.success for result in results)

        # Verify metrics
        metrics = provider_context.get_strategy_metrics("concurrent")
        assert metrics.total_operations == 50
        assert metrics.successful_operations == 50

    def test_provider_context_failover_scenario(self, provider_context):
        """Test provider failover scenario."""
        from src.providers.base.strategy.provider_strategy import (
            ProviderCapabilities,
            ProviderHealthStatus,
            ProviderResult,
        )

        # Create primary and backup strategies
        primary_strategy = Mock()
        primary_strategy.provider_type = "primary"
        primary_strategy.initialize.return_value = True
        primary_strategy.is_initialized.return_value = True
        primary_strategy.get_capabilities.return_value = ProviderCapabilities(
            supported_operations=[ProviderOperationType.CREATE_INSTANCES],
            provider_type="test",
            features={},
            limitations={},
            performance_metrics={},
        )
        primary_strategy.check_health.return_value = ProviderHealthStatus.unhealthy(
            "Service down")

        backup_strategy = Mock()
        backup_strategy.provider_type = "backup"
        backup_strategy.initialize.return_value = True
        backup_strategy.is_initialized.return_value = True
        backup_strategy.get_capabilities.return_value = ProviderCapabilities(
            supported_operations=[ProviderOperationType.CREATE_INSTANCES],
            provider_type="test",
            features={},
            limitations={},
            performance_metrics={},
        )
        backup_strategy.check_health.return_value = ProviderHealthStatus.healthy()
        backup_strategy.execute_operation.return_value = ProviderResult.success_result(
            {"provider": "backup"}
        )

        provider_context.register_strategy(primary_strategy)
        provider_context.register_strategy(backup_strategy)
        provider_context.initialize()

        # Check health of primary (should be unhealthy)
        primary_health = provider_context.check_strategy_health("primary")
        assert primary_health.is_healthy is False

        # Check health of backup (should be healthy)
        backup_health = provider_context.check_strategy_health("backup")
        assert backup_health.is_healthy is True

        # Execute operation on backup
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 1},
        )

        result = provider_context.execute_with_strategy("backup", operation)
        assert result.success is True
        assert result.data["provider"] == "backup"
