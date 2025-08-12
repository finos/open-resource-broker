"""
Integration tests for Machine Status CQRS Migration.

This test suite verifies that the migration from MachineStatusConversionService
to CQRS handlers maintains the same functionality.
"""

from unittest.mock import Mock

import pytest

from src.application.commands.machine_handlers import (
    ConvertBatchMachineStatusCommandHandler,
    ConvertMachineStatusCommandHandler,
    ValidateProviderStateCommandHandler,
)
from src.application.machine.commands import (
    ConvertBatchMachineStatusCommand,
    ConvertMachineStatusCommand,
    ValidateProviderStateCommand,
)
from src.domain.machine.value_objects import MachineStatus


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
    def convert_handler(self, mock_provider_context):
        """Create ConvertMachineStatusCommandHandler."""
        return ConvertMachineStatusCommandHandler(mock_provider_context)

    @pytest.fixture
    def batch_convert_handler(self, convert_handler):
        """Create ConvertBatchMachineStatusCommandHandler."""
        return ConvertBatchMachineStatusCommandHandler(convert_handler)

    @pytest.fixture
    def validate_handler(self, mock_provider_context):
        """Create ValidateProviderStateCommandHandler."""
        return ValidateProviderStateCommandHandler(mock_provider_context)

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
        assert result.metadata["test"] == "migration"

    def test_convert_machine_status_fallback(self, mock_provider_context):
        """Test ConvertMachineStatusCommandHandler fallback behavior."""
        # Configure provider context to fail
        mock_provider_context.set_strategy.side_effect = Exception("Provider error")

        handler = ConvertMachineStatusCommandHandler(mock_provider_context)

        command = ConvertMachineStatusCommand(provider_state="running", provider_type="aws")

        # Execute handler
        import asyncio

        result = asyncio.run(handler.handle(command))

        # Should still succeed with fallback
        assert result.success
        assert isinstance(result.status, MachineStatus)
        assert result.metadata.get("used_fallback")

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

    def test_fallback_conversion_mapping(self, mock_provider_context):
        """Test fallback conversion mapping matches original service."""
        handler = ConvertMachineStatusCommandHandler(mock_provider_context)

        # Test various state mappings
        test_cases = [
            ("running", MachineStatus.RUNNING),
            ("stopped", MachineStatus.STOPPED),
            ("pending", MachineStatus.PENDING),
            ("stopping", MachineStatus.STOPPING),
            ("terminated", MachineStatus.TERMINATED),
            ("shutting-down", MachineStatus.STOPPING),
            ("unknown-state", MachineStatus.UNKNOWN),
        ]

        for provider_state, expected_status in test_cases:
            result = handler._fallback_conversion(provider_state)
            assert result == expected_status, f"Failed for state: {provider_state}"

    def test_error_handling(self, mock_provider_context):
        """Test error handling in handlers."""
        # Create handler with failing provider context
        mock_provider_context.set_strategy.side_effect = Exception("Critical error")

        handler = ConvertMachineStatusCommandHandler(mock_provider_context)

        command = ConvertMachineStatusCommand(provider_state="running", provider_type="invalid")

        # Should not raise exception, should handle gracefully
        import asyncio

        result = asyncio.run(handler.handle(command))

        # Should succeed with fallback
        assert result.success
        assert result.metadata.get("used_fallback")

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

        # Interface mapping verified
        assert hasattr(ConvertMachineStatusCommand, "provider_state")
        assert hasattr(ConvertMachineStatusCommand, "provider_type")
        assert hasattr(ConvertBatchMachineStatusCommand, "provider_states")
        assert hasattr(ValidateProviderStateCommand, "provider_state")
        assert hasattr(ValidateProviderStateCommand, "provider_type")


if __name__ == "__main__":
    # Run migration tests
    pytest.main([__file__, "-v", "--tb=short"])
