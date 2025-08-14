"""Integration tests for provider strategy dry-run functionality."""

from unittest.mock import MagicMock, Mock, patch

from src.providers.aws.configuration.config import AWSProviderConfig
from src.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy
from src.providers.base.strategy import ProviderOperation, ProviderOperationType


class TestProviderStrategyDryRun:
    """Test provider strategy dry-run integration."""

    def setup_method(self):
        """Set up test fixtures."""
        # Mock dependencies
        self.mock_logger = Mock()

        # Create a proper AWSProviderConfig
        self.mock_config = AWSProviderConfig(region="us-east-1", profile="default")

        # Create AWS provider strategy
        self.aws_strategy = AWSProviderStrategy(
            config=self.mock_config, logger=self.mock_logger)

        # Mock the initialization to avoid real AWS client creation
        with patch.object(self.aws_strategy, "initialize", return_value=True):
            self.aws_strategy.initialize()

        # Set initialized flag manually
        self.aws_strategy._initialized = True

        # Mock the internal managers
        self.mock_instance_manager = Mock()
        self.aws_strategy._instance_manager = self.mock_instance_manager

    @patch("src.providers.aws.infrastructure.dry_run_adapter.aws_dry_run_context")
    def test_provider_operation_without_dry_run_context(self, mock_dry_run_context):
        """Test provider operation execution without dry-run context."""
        # Mock instance manager response
        self.mock_instance_manager.create_instances.return_value = [
            "i-1234567890abcdef0"]

        # Create operation without dry-run context
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": {"vm_type": "t2.micro", "image_id": "ami-12345678"},
                "count": 1,
            },
            context=None,
        )

        # Execute operation
        result = self.aws_strategy.execute_operation(operation)

        # Verify result
        assert result.success is True
        assert result.metadata["dry_run"] is False
        assert result.metadata["provider"] == "aws"
        assert "execution_time_ms" in result.metadata

        # Verify dry-run context was not used
        mock_dry_run_context.assert_not_called()

        # Verify instance manager was called
        self.mock_instance_manager.create_instances.assert_called_once()

    @patch("src.providers.aws.infrastructure.dry_run_adapter.aws_dry_run_context")
    def test_provider_operation_with_dry_run_context(self, mock_dry_run_context):
        """Test provider operation execution with dry-run context."""
        # Mock instance manager response
        self.mock_instance_manager.create_instances.return_value = [
            "i-1234567890abcdef0"]

        # Mock the context manager
        mock_context_manager = MagicMock()
        mock_dry_run_context.return_value = mock_context_manager

        # Create operation with dry-run context
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": {"vm_type": "t2.micro", "image_id": "ami-12345678"},
                "count": 1,
            },
            context={"dry_run": True},
        )

        # Execute operation
        result = self.aws_strategy.execute_operation(operation)

        # Verify result
        assert result.success is True
        assert result.metadata["dry_run"] is True
        assert result.metadata["provider"] == "aws"
        assert "execution_time_ms" in result.metadata

        # Verify dry-run context was used
        mock_dry_run_context.assert_called_once()
        mock_context_manager.__enter__.assert_called_once()
        mock_context_manager.__exit__.assert_called_once()

        # Verify instance manager was called (within dry-run context)
        self.mock_instance_manager.create_instances.assert_called_once()

    def test_provider_operation_error_handling_with_dry_run(self):
        """Test provider operation error handling with dry-run context."""
        # Mock instance manager to raise exception
        self.mock_instance_manager.create_instances.side_effect = Exception(
            "Test error")

        # Create operation with dry-run context
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": {"vm_type": "t2.micro", "image_id": "ami-12345678"},
                "count": 1,
            },
            context={"dry_run": True},
        )

        # Execute operation
        result = self.aws_strategy.execute_operation(operation)

        # Verify error result
        assert result.success is False
        assert result.metadata["dry_run"] is True
        assert result.metadata["provider"] == "aws"
        assert "execution_time_ms" in result.metadata
        assert "Test error" in result.error_message

    def test_unsupported_operation_with_dry_run(self):
        """Test unsupported operation handling with dry-run context."""
        # Create operation with unsupported type
        operation = ProviderOperation(
            operation_type="UNSUPPORTED_OPERATION",  # Invalid operation type
            parameters={},
            context={"dry_run": True},
        )

        # Execute operation
        result = self.aws_strategy.execute_operation(operation)

        # Verify error result
        assert result.success is False
        assert result.metadata["dry_run"] is True
        assert result.metadata["provider"] == "aws"
        assert "Unsupported operation" in result.error_message

    @patch("src.providers.aws.infrastructure.dry_run_adapter.aws_dry_run_context")
    def test_multiple_operations_with_mixed_dry_run_contexts(self, mock_dry_run_context):
        """Test multiple operations with different dry-run contexts."""
        # Mock instance manager responses
        self.mock_instance_manager.create_instances.return_value = [
            "i-1234567890abcdef0"]
        self.mock_instance_manager.terminate_instances.return_value = True

        # Mock the context manager
        mock_context_manager = MagicMock()
        mock_dry_run_context.return_value = mock_context_manager

        # Create operations with different dry-run contexts
        create_operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": {"vm_type": "t2.micro", "image_id": "ami-12345678"},
                "count": 1,
            },
            context={"dry_run": True},
        )

        terminate_operation = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={"instance_ids": ["i-1234567890abcdef0"]},
            context={"dry_run": False},
        )

        # Execute operations
        create_result = self.aws_strategy.execute_operation(create_operation)
        terminate_result = self.aws_strategy.execute_operation(terminate_operation)

        # Verify results
        assert create_result.success is True
        assert create_result.metadata["dry_run"] is True

        assert terminate_result.success is True
        assert terminate_result.metadata["dry_run"] is False

        # Verify dry-run context was used only once (for create operation)
        mock_dry_run_context.assert_called_once()

        # Verify both managers were called
        self.mock_instance_manager.create_instances.assert_called_once()
        self.mock_instance_manager.terminate_instances.assert_called_once()
