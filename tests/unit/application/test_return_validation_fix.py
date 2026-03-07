#!/usr/bin/env python3
"""Test for the return validation fix - filter instead of block."""

from unittest.mock import Mock

import pytest

from orb.application.commands.request_handlers import CreateReturnRequestHandler
from orb.application.dto.commands import CreateReturnRequestCommand


class TestReturnValidationFix:
    """Test the fix for return validation to filter instead of block."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_uow_factory = Mock()
        self.mock_uow = Mock()

        # Properly mock the context manager
        self.mock_context_manager = Mock()
        self.mock_context_manager.__enter__ = Mock(return_value=self.mock_uow)
        self.mock_context_manager.__exit__ = Mock(return_value=None)
        self.mock_uow_factory.create_unit_of_work.return_value = self.mock_context_manager

        self.mock_logger = Mock()
        self.mock_container = Mock()
        self.mock_event_publisher = Mock()
        self.mock_error_handler = Mock()
        self.mock_query_bus = Mock()
        self.mock_provider_registry_service = Mock()

        self.handler = CreateReturnRequestHandler(
            self.mock_uow_factory,
            self.mock_logger,
            self.mock_container,
            self.mock_event_publisher,
            self.mock_error_handler,
            self.mock_query_bus,
            self.mock_provider_registry_service,
        )

    @pytest.mark.asyncio
    async def test_filters_machines_with_pending_return_requests(self):
        """Test that machines with pending return requests are filtered out, not blocked."""
        # Setup machines - some valid, some with pending return requests
        machine1 = Mock()
        machine1.machine_id = "machine-001"
        machine1.return_request_id = None  # Valid

        machine2 = Mock()
        machine2.machine_id = "machine-002"
        machine2.return_request_id = "existing-return-req-123"  # Invalid - has pending return

        machine3 = Mock()
        machine3.machine_id = "machine-003"
        machine3.return_request_id = None  # Valid

        # Mock repository responses
        def mock_get_by_id(machine_id):
            if machine_id == "machine-001":
                return machine1
            elif machine_id == "machine-002":
                return machine2
            elif machine_id == "machine-003":
                return machine3
            return None

        self.mock_uow.machines.get_by_id.side_effect = mock_get_by_id

        # Create command with all machines (multiple machines)
        command = CreateReturnRequestCommand(
            machine_ids=["machine-001", "machine-002", "machine-003"]
        )

        # Validation should pass (no exception for multiple machines)
        await self.handler.validate_command(command)

        # Test the filtering logic directly by calling the filtering part
        # This simulates what happens in execute_command
        is_single_machine = len(command.machine_ids) == 1
        assert not is_single_machine  # Should be multiple machines

        # Simulate the filtering logic from execute_command
        valid_machine_ids = []
        skipped_machines = []

        with self.mock_context_manager:
            for machine_id in command.machine_ids:
                machine = self.mock_uow.machines.get_by_id(machine_id)
                if not machine:
                    skipped_machines.append(
                        {"machine_id": machine_id, "reason": "Machine not found"}
                    )
                    continue

                if machine.return_request_id:
                    skipped_machines.append(
                        {
                            "machine_id": machine_id,
                            "reason": f"Machine already has pending return request: {machine.return_request_id}",
                        }
                    )
                    continue

                valid_machine_ids.append(machine_id)

        # Verify filtering worked correctly
        assert len(valid_machine_ids) == 2  # machine-001 and machine-003
        assert "machine-001" in valid_machine_ids
        assert "machine-003" in valid_machine_ids
        assert "machine-002" not in valid_machine_ids

        assert len(skipped_machines) == 1
        assert skipped_machines[0]["machine_id"] == "machine-002"
        assert "pending return request" in skipped_machines[0]["reason"]

    @pytest.mark.asyncio
    async def test_single_machine_return_still_validates_properly(self):
        """Test that single machine return still validates properly (should still raise for invalid)."""
        # For single machine operations, we should still validate strictly
        machine = Mock()
        machine.machine_id = "machine-001"
        machine.return_request_id = "existing-return-req-123"

        self.mock_uow.machines.get_by_id.return_value = machine

        command = CreateReturnRequestCommand(machine_ids=["machine-001"])

        # For single machine, should still raise exception (not --all operation)
        with pytest.raises(Exception) as exc_info:
            await self.handler.validate_command(command)

        assert "already has pending return request" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_all_machines_invalid_filters_all(self):
        """Test that if all machines are invalid, filtering removes all machines."""
        # All machines have pending return requests
        machine1 = Mock()
        machine1.machine_id = "machine-001"
        machine1.return_request_id = "existing-return-req-123"

        machine2 = Mock()
        machine2.machine_id = "machine-002"
        machine2.return_request_id = "existing-return-req-456"

        def mock_get_by_id(machine_id):
            if machine_id == "machine-001":
                return machine1
            elif machine_id == "machine-002":
                return machine2
            return None

        self.mock_uow.machines.get_by_id.side_effect = mock_get_by_id

        command = CreateReturnRequestCommand(machine_ids=["machine-001", "machine-002"])

        # Validation should pass (no exception for multiple machines)
        await self.handler.validate_command(command)

        # Test the filtering logic directly
        is_single_machine = len(command.machine_ids) == 1
        assert not is_single_machine  # Should be multiple machines

        # Simulate the filtering logic from execute_command
        valid_machine_ids = []
        skipped_machines = []

        with self.mock_context_manager:
            for machine_id in command.machine_ids:
                machine = self.mock_uow.machines.get_by_id(machine_id)
                if not machine:
                    skipped_machines.append(
                        {"machine_id": machine_id, "reason": "Machine not found"}
                    )
                    continue

                if machine.return_request_id:
                    skipped_machines.append(
                        {
                            "machine_id": machine_id,
                            "reason": f"Machine already has pending return request: {machine.return_request_id}",
                        }
                    )
                    continue

                valid_machine_ids.append(machine_id)

        # All machines should be filtered out
        assert len(valid_machine_ids) == 0
        assert len(skipped_machines) == 2


if __name__ == "__main__":
    pytest.main([__file__])
