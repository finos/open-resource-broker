"""End-to-end integration tests for dry-run functionality."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from application.commands.request_handlers import CreateMachineRequestHandler
from application.dto.commands import CreateRequestCommand
from config.manager import ConfigurationManager
from domain.request.aggregate import Request
from domain.request.value_objects import RequestId, RequestType
from domain.template.template_aggregate import Template
from infrastructure.adapters.ports.resource_provisioning_port import ResourceProvisioningPort
from infrastructure.mocking.dry_run_context import dry_run_context
from providers.aws.configuration.config import AWSProviderConfig
from providers.aws.infrastructure.adapters import AWSProvisioningAdapter
from providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy


class TestEndToEndDryRun:
    """Test end-to-end dry-run functionality from command to provider strategy."""

    def setup_method(self):
        """Set up test fixtures."""
        # Mock dependencies
        self.mock_logger = Mock()
        self.mock_uow_factory = Mock()
        self.mock_event_publisher = Mock()
        self.mock_error_handler = Mock()
        self.mock_container = Mock()
        self.mock_query_bus = Mock()
        self.mock_provider_selection_service = Mock()
        self.mock_provider_capability_service = Mock()
        self.mock_provider_port = Mock()

        # Create AWS provider strategy
        self.aws_config = AWSProviderConfig(region="us-east-1", profile="default")
        self.aws_strategy = AWSProviderStrategy(config=self.aws_config, logger=self.mock_logger)

        # Mock strategy initialization
        with patch.object(self.aws_strategy, "initialize", return_value=True):
            self.aws_strategy.initialize()
        self.aws_strategy._initialized = True

        # Mock internal managers
        self.mock_instance_manager = Mock()
        self.aws_strategy._instance_manager = self.mock_instance_manager

        # Create provisioning adapter with strategy
        self.mock_aws_client = Mock()
        self.mock_handler_factory = Mock()
        self.provisioning_adapter = AWSProvisioningAdapter(
            aws_client=self.mock_aws_client,
            logger=self.mock_logger,
            aws_handler_factory=self.mock_handler_factory,
            provider_strategy=self.aws_strategy,
        )

        # Mock the provisioning adapter methods to track calls
        self.provisioning_adapter.provision_resources = AsyncMock(
            wraps=self.provisioning_adapter.provision_resources
        )
        self.provisioning_adapter._provision_via_strategy = AsyncMock(
            wraps=self.provisioning_adapter._provision_via_strategy
        )
        self.provisioning_adapter._provision_via_handlers = Mock(
            wraps=self.provisioning_adapter._provision_via_handlers
        )

        # Mock container to return appropriate services
        mock_config_manager = Mock()
        mock_config_manager.get.return_value = "aws"

        def container_get(service_type):
            if service_type == ConfigurationManager:
                return mock_config_manager
            elif service_type == ResourceProvisioningPort:
                return self.provisioning_adapter
            else:
                return Mock()

        self.mock_container.get.side_effect = container_get

        # Mock UoW factory to return mock UoW with repositories
        mock_uow = Mock()
        mock_uow.request_repository = Mock()
        mock_uow.machine_repository = Mock()
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        self.mock_uow_factory.create_unit_of_work.return_value = mock_uow
        self.mock_uow = mock_uow

        # Create command handler
        self.command_handler = CreateMachineRequestHandler(
            uow_factory=self.mock_uow_factory,
            logger=self.mock_logger,
            container=self.mock_container,
            event_publisher=self.mock_event_publisher,
            error_handler=self.mock_error_handler,
            query_bus=self.mock_query_bus,
            provider_selection_service=self.mock_provider_selection_service,
            provider_capability_service=self.mock_provider_capability_service,
            provider_port=self.mock_provider_port,
        )

    @patch("providers.aws.infrastructure.dry_run_adapter.aws_dry_run_context")
    def test_dry_run_command_propagates_to_provider_strategy(self, mock_dry_run_context):
        """Test that dry-run context from command propagates to provider strategy."""
        # Mock template query response
        mock_template = Template(
            template_id="test-template",
            provider_api="EC2Fleet",
            vm_type="t2.micro",
            image_id="ami-12345678",
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
            max_number=10,
        )

        mock_template_dto = Mock()
        mock_template_dto.model_dump.return_value = mock_template.model_dump()
        self.mock_query_bus.dispatch.return_value = mock_template_dto

        # Mock instance manager response
        self.mock_instance_manager.create_instances.return_value = ["i-1234567890abcdef0"]

        # Mock dry-run context manager
        mock_context_manager = MagicMock()
        mock_dry_run_context.return_value = mock_context_manager

        # Mock request repository save
        self.mock_uow.request_repository.save.return_value = []

        # Create command with dry-run enabled
        command = CreateRequestCommand(
            template_id="test-template",
            requested_count=1,
            metadata={"test": "data"},
            dry_run=True,  # Enable dry-run
        )

        # Execute command
        request_id = self.command_handler.handle(command)

        # Verify request was created
        assert request_id is not None

        # Verify request repository was called with dry-run in metadata
        self.mock_uow.request_repository.save.assert_called_once()
        saved_request = self.mock_uow.request_repository.save.call_args[0][0]
        assert saved_request.metadata["dry_run"] is True

        # Verify dry-run context was used in provider strategy
        mock_dry_run_context.assert_called_once()
        mock_context_manager.__enter__.assert_called_once()
        mock_context_manager.__exit__.assert_called_once()

        # Verify instance manager was called within dry-run context
        self.mock_instance_manager.create_instances.assert_called_once()

    def test_normal_command_does_not_use_dry_run(self):
        """Test that normal commands do not trigger dry-run mode."""
        # Mock template query response
        mock_template_dto = Mock()
        mock_template_dto.model_dump.return_value = {
            "template_id": "test-template",
            "vm_type": "t2.micro",
            "image_id": "ami-12345678",
        }
        self.mock_query_bus.dispatch.return_value = mock_template_dto

        # Mock legacy handler for normal operations
        mock_handler = Mock()
        mock_handler.acquire_hosts.return_value = "fleet-12345"
        self.mock_handler_factory.get_handler.return_value = mock_handler

        # Mock request repository save
        self.mock_uow.request_repository.save.return_value = []

        # Create command without dry-run
        command = CreateRequestCommand(
            template_id="test-template",
            requested_count=1,
            metadata={"test": "data"},
            dry_run=False,  # Normal operation
        )

        # Execute command
        request_id = self.command_handler.handle(command)

        # Verify request was created
        assert request_id is not None

        # Verify request repository was called without dry-run in metadata
        self.mock_uow.request_repository.save.assert_called_once()
        saved_request = self.mock_uow.request_repository.save.call_args[0][0]
        assert saved_request.metadata["dry_run"] is False

        # Verify legacy handler was used (not provider strategy)
        mock_handler.acquire_hosts.assert_called_once()

        # Verify instance manager was NOT called (legacy path used)
        self.mock_instance_manager.create_instances.assert_not_called()

    @patch("providers.aws.infrastructure.dry_run_adapter.aws_dry_run_context")
    def test_global_dry_run_context_with_command_dry_run(self, mock_dry_run_context):
        """Test interaction between global dry-run context and command dry-run flag."""
        # Mock template query response
        mock_template_dto = Mock()
        mock_template_dto.model_dump.return_value = {
            "template_id": "test-template",
            "vm_type": "t2.micro",
            "image_id": "ami-12345678",
        }
        self.mock_query_bus.dispatch.return_value = mock_template_dto

        # Mock instance manager response
        self.mock_instance_manager.create_instances.return_value = ["i-1234567890abcdef0"]

        # Mock dry-run context manager
        mock_context_manager = MagicMock()
        mock_dry_run_context.return_value = mock_context_manager

        # Mock request repository save
        self.mock_uow.request_repository.save.return_value = []

        # Create command with dry-run enabled
        command = CreateRequestCommand(
            template_id="test-template",
            requested_count=1,
            metadata={"test": "data"},
            dry_run=True,
        )

        # Execute command within global dry-run context
        with dry_run_context(True):
            request_id = self.command_handler.handle(command)

        # Verify request was created
        assert request_id is not None

        # Verify both global and command dry-run contexts are respected
        self.mock_uow.request_repository.save.assert_called_once()
        saved_request = self.mock_uow.request_repository.save.call_args[0][0]
        assert saved_request.metadata["dry_run"] is True

        # Verify provider strategy dry-run context was used
        mock_dry_run_context.assert_called_once()

    def test_provisioning_adapter_strategy_selection(self):
        """Test that provisioning adapter correctly selects strategy vs handlers."""
        # Create test request with dry-run metadata
        request = Request(
            request_id=RequestId.generate(RequestType.ACQUIRE),
            request_type=RequestType.ACQUIRE,
            provider_type="aws",
            template_id="test-template",
            requested_count=1,
            metadata={"dry_run": True},
        )

        # Create test template
        template = Template(
            template_id="test-template",
            provider_api="EC2Fleet",
            vm_type="t2.micro",
            image_id="ami-12345678",
        )

        # Mock instance manager response for strategy path
        self.mock_instance_manager.create_instances.return_value = ["i-1234567890abcdef0"]

        # Execute provisioning
        resource_id = asyncio.run(self.provisioning_adapter.provision_resources(request, template))

        # Verify strategy path was used
        assert resource_id == "i-1234567890abcdef0"
        self.mock_instance_manager.create_instances.assert_called_once()

        # Verify handler factory was NOT used
        self.mock_handler_factory.get_handler.assert_not_called()

    def test_provisioning_adapter_handler_selection(self):
        """Test that provisioning adapter uses handlers for normal operations."""
        # Create test request without dry-run metadata
        request = Request(
            request_id=RequestId.generate(RequestType.ACQUIRE),
            request_type=RequestType.ACQUIRE,
            provider_type="aws",
            template_id="test-template",
            requested_count=1,
            metadata={"dry_run": False},
        )

        # Create test template
        template = Template(
            template_id="test-template",
            provider_api="EC2Fleet",
            vm_type="t2.micro",
            image_id="ami-12345678",
        )

        # Mock handler response for legacy path
        mock_handler = Mock()
        mock_handler.acquire_hosts.return_value = "fleet-12345"
        self.mock_handler_factory.get_handler.return_value = mock_handler

        # Execute provisioning
        resource_id = asyncio.run(self.provisioning_adapter.provision_resources(request, template))

        # Verify handler path was used
        assert resource_id == "fleet-12345"
        mock_handler.acquire_hosts.assert_called_once()

        # Verify strategy was NOT used
        self.mock_instance_manager.create_instances.assert_not_called()
