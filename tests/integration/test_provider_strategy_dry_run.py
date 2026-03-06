"""Integration tests for provider strategy dry-run functionality."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType


class TestProviderStrategyDryRun:
    """Test provider strategy dry-run integration."""

    def setup_method(self):
        """Set up test fixtures."""
        # Mock dependencies
        self.mock_logger = Mock()

        # Create a appropriate AWSProviderConfig
        self.mock_config = AWSProviderConfig(region="us-east-1", profile="default")

        # Create AWS provider strategy
        self.aws_strategy = AWSProviderStrategy(config=self.mock_config, logger=self.mock_logger)
        self.aws_strategy._initialized = True

        # Mock the handler
        self.mock_handler = Mock()
        self.mock_handler.acquire_hosts.return_value = {
            "resource_ids": ["i-1234567890abcdef0"],
            "instances": [],
            "success": True,
        }

        # Mock handler registry to return our mock handler
        mock_handler_registry = Mock()
        mock_handler_registry.get_available_handlers.return_value = {
            "RunInstances": self.mock_handler
        }
        self.aws_strategy._handler_registry = mock_handler_registry

        # Mock instance service to use our mock handler registry
        mock_instance_service = Mock()

        async def _mock_create_instances(operation, handlers):
            handler = handlers.get(
                operation.parameters.get("template_config", {}).get("provider_api", "RunInstances")
            ) or handlers.get("RunInstances")
            if handler is None:
                from orb.providers.base.strategy import ProviderResult

                return ProviderResult.error_result("No handler found", "HANDLER_NOT_FOUND")
            result = handler.acquire_hosts(None, None)
            if isinstance(result, Exception):
                raise result
            from orb.providers.base.strategy import ProviderResult

            return ProviderResult.success_result(
                {"resource_ids": result.get("resource_ids", []), "instances": []},
                {"method": "handler"},
            )

        mock_instance_service.create_instances = _mock_create_instances

        def _mock_terminate(operation):
            from orb.providers.base.strategy import ProviderResult

            instance_ids = operation.parameters.get("instance_ids", [])
            return ProviderResult.success_result(
                {"terminated": instance_ids}, {"method": "terminate"}
            )

        mock_instance_service.terminate_instances = _mock_terminate
        self.aws_strategy._instance_service = mock_instance_service

    @pytest.mark.asyncio
    @patch("providers.aws.infrastructure.dry_run_adapter.aws_dry_run_context")
    async def test_provider_operation_without_dry_run_context(self, mock_dry_run_context):
        """Test provider operation execution without dry-run context."""
        # Create operation without dry-run context
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": {
                    "template_id": "test-template",
                    "machine_types": {"t2.micro": 1},
                    "image_id": "ami-12345678",
                    "provider_api": "RunInstances",
                    "subnet_ids": ["subnet-12345"],
                    "security_group_ids": ["sg-12345"],
                },
                "count": 1,
            },
            context=None,
        )

        # Execute operation
        result = await self.aws_strategy.execute_operation(operation)

        # Verify result
        assert result.success is True
        assert result.metadata["dry_run"] is False
        assert result.metadata["provider"] == "aws"
        assert "execution_time_ms" in result.metadata

        # Verify dry-run context was not used
        mock_dry_run_context.assert_not_called()

        # Verify handler was called
        self.mock_handler.acquire_hosts.assert_called_once()

    @pytest.mark.asyncio
    @patch("providers.aws.infrastructure.dry_run_adapter.aws_dry_run_context")
    async def test_provider_operation_with_dry_run_context(self, mock_dry_run_context):
        """Test provider operation execution with dry-run context."""
        # Mock the context manager
        mock_context_manager = MagicMock()
        mock_dry_run_context.return_value = mock_context_manager

        # Create operation with dry-run context
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": {
                    "template_id": "test-template",
                    "machine_types": {"t2.micro": 1},
                    "image_id": "ami-12345678",
                    "provider_api": "RunInstances",
                    "subnet_ids": ["subnet-12345"],
                    "security_group_ids": ["sg-12345"],
                },
                "count": 1,
            },
            context={"dry_run": True},
        )

        # Execute operation
        result = await self.aws_strategy.execute_operation(operation)

        # Verify result
        assert result.success is True
        assert result.metadata["dry_run"] is True
        assert result.metadata["provider"] == "aws"
        assert "execution_time_ms" in result.metadata

        # Verify dry-run context was used
        mock_dry_run_context.assert_called_once()
        mock_context_manager.__enter__.assert_called_once()
        mock_context_manager.__exit__.assert_called_once()

        # Verify handler was called (within dry-run context)
        self.mock_handler.acquire_hosts.assert_called_once()

    @pytest.mark.asyncio
    async def test_provider_operation_error_handling_with_dry_run(self):
        """Test provider operation error handling with dry-run context."""
        # Mock handler to raise exception
        self.mock_handler.acquire_hosts.side_effect = Exception("Test error")

        # Create operation with dry-run context
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": {
                    "template_id": "test-template",
                    "machine_types": {"t2.micro": 1},
                    "image_id": "ami-12345678",
                    "provider_api": "RunInstances",
                    "subnet_ids": ["subnet-12345"],
                    "security_group_ids": ["sg-12345"],
                },
                "count": 1,
            },
            context={"dry_run": True},
        )

        # Execute operation
        result = await self.aws_strategy.execute_operation(operation)

        # Verify error result
        assert result.success is False
        assert result.metadata["dry_run"] is True
        assert result.metadata["provider"] == "aws"
        assert "execution_time_ms" in result.metadata
        assert "Test error" in result.error_message

    @pytest.mark.asyncio
    async def test_unsupported_operation_with_dry_run(self):
        """Test unsupported operation handling with dry-run context."""
        # Create operation with unsupported type
        operation = ProviderOperation(
            operation_type="UNSUPPORTED_OPERATION",  # Invalid operation type
            parameters={},
            context={"dry_run": True},
        )

        # Execute operation
        result = await self.aws_strategy.execute_operation(operation)

        # Verify error result
        assert result.success is False
        assert result.metadata["dry_run"] is True
        assert result.metadata["provider"] == "aws"
        assert "Unsupported operation" in result.error_message

    @pytest.mark.asyncio
    @patch("providers.aws.infrastructure.dry_run_adapter.aws_dry_run_context")
    async def test_multiple_operations_with_mixed_dry_run_contexts(self, mock_dry_run_context):
        """Test multiple operations with different dry-run contexts."""
        # Mock the context manager
        mock_context_manager = MagicMock()
        mock_dry_run_context.return_value = mock_context_manager

        # Mock AWS client for terminate operation
        mock_ec2_client = Mock()
        mock_ec2_client.terminate_instances.return_value = {
            "TerminatingInstances": [{"InstanceId": "i-1234567890abcdef0"}]
        }
        mock_aws_client = Mock()
        mock_aws_client.ec2_client = mock_ec2_client
        self.aws_strategy._aws_client = mock_aws_client

        # Create operations with different dry-run contexts
        create_operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": {
                    "template_id": "test-template",
                    "machine_types": {"t2.micro": 1},
                    "image_id": "ami-12345678",
                    "provider_api": "RunInstances",
                    "subnet_ids": ["subnet-12345"],
                    "security_group_ids": ["sg-12345"],
                },
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
        create_result = await self.aws_strategy.execute_operation(create_operation)
        terminate_result = await self.aws_strategy.execute_operation(terminate_operation)

        # Verify results
        assert create_result.success is True
        assert create_result.metadata["dry_run"] is True

        assert terminate_result.success is True
        assert terminate_result.metadata["dry_run"] is False

        # Verify dry-run context was used only once (for create operation)
        mock_dry_run_context.assert_called_once()

        # Verify handlers were called
        self.mock_handler.acquire_hosts.assert_called_once()
