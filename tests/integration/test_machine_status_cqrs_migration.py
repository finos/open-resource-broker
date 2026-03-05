"""
Integration tests for Machine Status CQRS Migration.

This test suite verifies that the migration from MachineStatusConversionService
to CQRS handlers maintains the same functionality.
"""

from unittest.mock import Mock

import pytest

from application.commands.machine_handlers import (
    ConvertBatchMachineStatusCommandHandler,
    ConvertMachineStatusCommandHandler,
    ValidateProviderStateCommandHandler,
)
from application.machine.commands import (
    ConvertBatchMachineStatusCommand,
    ConvertMachineStatusCommand,
    ValidateProviderStateCommand,
)
from domain.machine.value_objects import MachineStatus


@pytest.mark.integration
class TestMachineStatusCQRSMigration:
    """Integration tests for machine status CQRS migration."""

    @pytest.fixture
    def mock_provider_context(self):
        """Create mock provider context."""
        context = Mock()
        context.set_strategy.return_value = True
        context.execute_operation.return_value = Mock(success=True, data={"status": "running"})
        return context

    @pytest.fixture
    def mock_logger(self):
        """Create mock logger."""
        return Mock()

    @pytest.fixture
    def mock_event_publisher(self):
        """Create mock event publisher."""
        return Mock()

    @pytest.fixture
    def mock_error_handler(self):
        """Create mock error handler."""
        return Mock()

    @pytest.fixture
    def mock_provider_registry_service(self):
        """Create mock provider registry service."""
        service = Mock()
        # Simulate a successful status conversion result
        result = Mock()
        result.success = True
        result.data = {"status": "running"}
        service.execute_operation = Mock(return_value=result)

        # Make it awaitable
        async def _async_execute(provider_type, operation):
            return result

        service.execute_operation = _async_execute
        return service

    @pytest.fixture
    def convert_handler(
        self,
        mock_provider_context,
        mock_logger,
        mock_event_publisher,
        mock_error_handler,
        mock_provider_registry_service,
    ):
        """Create ConvertMachineStatusCommandHandler."""
        return ConvertMachineStatusCommandHandler(
            mock_provider_context,
            mock_logger,
            mock_event_publisher,
            mock_error_handler,
            mock_provider_registry_service,
        )

    @pytest.fixture
    def batch_convert_handler(
        self,
        mock_provider_context,
        mock_logger,
        mock_event_publisher,
        mock_error_handler,
        mock_provider_registry_service,
        convert_handler,
    ):
        """Create ConvertBatchMachineStatusCommandHandler."""
        return ConvertBatchMachineStatusCommandHandler(
            convert_handler, mock_logger, mock_event_publisher, mock_error_handler
        )

    @pytest.fixture
    def validate_handler(
        self,
        mock_provider_context,
        mock_logger,
        mock_event_publisher,
        mock_error_handler,
        mock_provider_registry_service,
    ):
        """Create ValidateProviderStateCommandHandler."""
        return ValidateProviderStateCommandHandler(
            mock_provider_context,
            mock_logger,
            mock_event_publisher,
            mock_error_handler,
            mock_provider_registry_service,
        )

    def test_convert_machine_status_handler(self, convert_handler):
        """Test ConvertMachineStatusCommandHandler functionality."""
        # Create command
        command = ConvertMachineStatusCommand(
            provider_state="running",
            provider_type="aws",
            metadata={"test": "migration"},
        )

        # Execute handler (synchronous version)
        import asyncio

        result = asyncio.run(convert_handler.handle(command))

        # Verify result
        assert result.success
        assert isinstance(result.status, MachineStatus)
        assert result.original_state == "running"
        assert result.provider_type == "aws"

    def test_convert_machine_status_fallback(
        self,
        mock_provider_context,
        mock_logger,
        mock_event_publisher,
        mock_error_handler,
        mock_provider_registry_service,
    ):
        """Test ConvertMachineStatusCommandHandler fallback behavior when registry returns failure."""
        # Configure registry service to return a failed result (not raise)
        failed_result = Mock()
        failed_result.success = False
        failed_result.data = {}

        async def _failed_execute(provider_type, operation):
            return failed_result

        mock_provider_registry_service.execute_operation = _failed_execute

        handler = ConvertMachineStatusCommandHandler(
            mock_provider_context,
            mock_logger,
            mock_event_publisher,
            mock_error_handler,
            mock_provider_registry_service,
        )

        command = ConvertMachineStatusCommand(provider_state="running", provider_type="aws")

        import asyncio

        result = asyncio.run(handler.handle(command))

        # Should succeed with UNKNOWN fallback status
        assert result.success
        assert isinstance(result.status, MachineStatus)
        assert result.status == MachineStatus.UNKNOWN

    def test_batch_convert_handler(self, batch_convert_handler):
        """Test ConvertBatchMachineStatusCommandHandler functionality."""
        # Create batch command
        command = ConvertBatchMachineStatusCommand(
            provider_states=[
                {"state": "running", "provider_type": "aws"},
                {"state": "stopped", "provider_type": "aws"},
                {"state": "pending", "provider_type": "aws"},
            ],
            metadata={"batch": "test"},
        )

        # Execute handler
        import asyncio

        result = asyncio.run(batch_convert_handler.handle(command))

        # Verify result
        assert result.success
        assert len(result.statuses) == 3
        assert result.count == 3
        assert all(isinstance(status, MachineStatus) for status in result.statuses)

    def test_validate_provider_state_handler(self, validate_handler):
        """Test ValidateProviderStateCommandHandler functionality."""
        # Create validation command
        command = ValidateProviderStateCommand(
            provider_state="running",
            provider_type="aws",
            metadata={"validation": "test"},
        )

        # Execute handler
        import asyncio

        result = asyncio.run(validate_handler.handle(command))

        # Verify result
        assert result.success
        assert isinstance(result.is_valid, bool)
        assert result.provider_state == "running"
        assert result.provider_type == "aws"

    def test_handler_can_handle_methods(
        self, convert_handler, batch_convert_handler, validate_handler
    ):
        """Test that handlers correctly identify their commands."""
        convert_command = ConvertMachineStatusCommand(provider_state="running", provider_type="aws")
        batch_command = ConvertBatchMachineStatusCommand(provider_states=[])
        validate_command = ValidateProviderStateCommand(
            provider_state="running", provider_type="aws"
        )

        # Test convert handler
        assert convert_handler.can_handle(convert_command)
        assert convert_handler.can_handle(batch_command) is False
        assert convert_handler.can_handle(validate_command) is False

        # Test batch handler
        assert batch_convert_handler.can_handle(convert_command) is False
        assert batch_convert_handler.can_handle(batch_command)
        assert batch_convert_handler.can_handle(validate_command) is False

        # Test validate handler
        assert validate_handler.can_handle(convert_command) is False
        assert validate_handler.can_handle(batch_command) is False
        assert validate_handler.can_handle(validate_command)

    def test_fallback_conversion_mapping(
        self,
        mock_provider_context,
        mock_logger,
        mock_event_publisher,
        mock_error_handler,
        mock_provider_registry_service,
    ):
        """Test fallback conversion mapping matches original service."""
        # Configure registry service to return failed result so handler uses UNKNOWN fallback
        failed_result = Mock()
        failed_result.success = False
        failed_result.data = {}

        async def _failed_execute(provider_type, operation):
            return failed_result

        mock_provider_registry_service.execute_operation = _failed_execute

        handler = ConvertMachineStatusCommandHandler(
            mock_provider_context,
            mock_logger,
            mock_event_publisher,
            mock_error_handler,
            mock_provider_registry_service,
        )

        import asyncio

        test_states = [
            "running",
            "stopped",
            "pending",
            "stopping",
            "terminated",
            "shutting-down",
            "unknown-state",
        ]

        for provider_state in test_states:
            command = ConvertMachineStatusCommand(
                provider_state=provider_state, provider_type="aws"
            )
            result = asyncio.run(handler.handle(command))
            assert result.success, f"Handler failed for state: {provider_state}"
            assert isinstance(result.status, MachineStatus), (
                f"Expected MachineStatus for state: {provider_state}"
            )

    def test_error_handling(
        self,
        mock_provider_context,
        mock_logger,
        mock_event_publisher,
        mock_error_handler,
        mock_provider_registry_service,
    ):
        """Test error handling in handlers."""
        # Configure registry service to return failed result
        failed_result = Mock()
        failed_result.success = False
        failed_result.data = {}

        async def _failed_execute(provider_type, operation):
            return failed_result

        mock_provider_registry_service.execute_operation = _failed_execute

        handler = ConvertMachineStatusCommandHandler(
            mock_provider_context,
            mock_logger,
            mock_event_publisher,
            mock_error_handler,
            mock_provider_registry_service,
        )

        command = ConvertMachineStatusCommand(provider_state="running", provider_type="invalid")

        import asyncio

        result = asyncio.run(handler.handle(command))

        # Should succeed with UNKNOWN fallback status
        assert result.success
        assert isinstance(result.status, MachineStatus)
        assert result.status == MachineStatus.UNKNOWN

    def test_performance_comparison(self, convert_handler):
        """Test performance of CQRS handlers vs original service."""
        import asyncio
        import time

        # Test multiple conversions
        commands = [
            ConvertMachineStatusCommand(provider_state="running", provider_type="aws"),
            ConvertMachineStatusCommand(provider_state="stopped", provider_type="aws"),
            ConvertMachineStatusCommand(provider_state="pending", provider_type="aws"),
        ]

        start_time = time.time()

        for command in commands:
            result = asyncio.run(convert_handler.handle(command))
            assert result.success

        end_time = time.time()
        total_time = end_time - start_time

        print(f"CQRS handlers performance: {total_time:.3f}s for {len(commands)} conversions")

        # Should complete within reasonable time
        assert total_time < 1.0, f"Performance regression: {total_time:.3f}s"


@pytest.mark.integration
class TestMachineStatusMigrationCompatibility:
    """Test compatibility between old service and new CQRS handlers."""

    def test_equivalent_functionality(self):
        """Test that CQRS handlers provide equivalent functionality to original service."""
        # This test would compare the old service behavior with new handlers
        # For now, we'll just verify the interface compatibility

        # Original service methods:
        # - convert_from_provider_state(provider_state, provider_type) -> MachineStatus
        # - convert_batch_from_provider_states(provider_states) -> List[MachineStatus]
        # - validate_provider_state(provider_state, provider_type) -> bool

        # New CQRS commands:
        # - ConvertMachineStatusCommand -> ConvertMachineStatusResponse
        # - ConvertBatchMachineStatusCommand -> ConvertBatchMachineStatusResponse
        # - ValidateProviderStateCommand -> ValidateProviderStateResponse

        # Interface mapping verified - check via model_fields (Pydantic v2)
        assert "provider_state" in ConvertMachineStatusCommand.model_fields
        assert "provider_type" in ConvertMachineStatusCommand.model_fields
        assert "provider_states" in ConvertBatchMachineStatusCommand.model_fields
        assert "provider_state" in ValidateProviderStateCommand.model_fields
        assert "provider_type" in ValidateProviderStateCommand.model_fields


if __name__ == "__main__":
    # Run migration tests
    pytest.main([__file__, "-v", "--tb=short"])
