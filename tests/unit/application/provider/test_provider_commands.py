"""Unit tests for provider strategy commands."""

from application.provider.commands import (
    ExecuteProviderOperationCommand,
    RegisterProviderStrategyCommand,
    SelectProviderStrategyCommand,
    UpdateProviderHealthCommand,
)
from providers.base.strategy import (
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    SelectionCriteria,
)


class TestProviderStrategyCommands:
    """Test provider strategy command creation and validation."""

    def test_select_provider_strategy_command_creation(self):
        """Test SelectProviderStrategyCommand creation."""
        criteria = SelectionCriteria()

        command = SelectProviderStrategyCommand(
            operation_type=ProviderOperationType.HEALTH_CHECK,
            selection_criteria=criteria,
            context={"test": "data"},
        )

        assert command.operation_type == ProviderOperationType.HEALTH_CHECK
        assert command.selection_criteria == criteria
        assert command.context == {"test": "data"}

    def test_execute_provider_operation_command_creation(self):
        """Test ExecuteProviderOperationCommand creation."""
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 2},
        )

        command = ExecuteProviderOperationCommand(
            operation=operation,
            strategy_override="aws-primary",
            retry_count=3,
            timeout_seconds=30,
        )

        assert command.operation == operation
        assert command.strategy_override == "aws-primary"
        assert command.retry_count == 3
        assert command.timeout_seconds == 30

    def test_register_provider_strategy_command_creation(self):
        """Test RegisterProviderStrategyCommand creation."""
        command = RegisterProviderStrategyCommand(
            strategy_name="aws-test",
            provider_type="aws",
            strategy_config={"region": "us-east-1"},
            capabilities={"instances": True},
            priority=1,
        )

        assert command.strategy_name == "aws-test"
        assert command.provider_type == "aws"
        assert command.strategy_config == {"region": "us-east-1"}
        assert command.capabilities == {"instances": True}
        assert command.priority == 1

    def test_update_provider_health_command_creation(self):
        """Test UpdateProviderHealthCommand creation."""
        health_status = ProviderHealthStatus.healthy("All systems operational")

        command = UpdateProviderHealthCommand(
            provider_name="aws-primary",
            health_status=health_status,
            source="health_monitor",
            timestamp="2025-07-02T14:00:00Z",
        )

        assert command.provider_name == "aws-primary"
        assert command.health_status == health_status
        assert command.source == "health_monitor"
        assert command.timestamp == "2025-07-02T14:00:00Z"
