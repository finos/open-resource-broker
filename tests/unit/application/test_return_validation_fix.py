#!/usr/bin/env python3
"""Test return-request validation and lifecycle behavior."""

from unittest.mock import AsyncMock, Mock

import pytest

from orb.application.commands.request_handlers import CreateReturnRequestHandler
from orb.application.dto.commands import CreateReturnRequestCommand
from orb.domain.request.request_types import RequestStatus


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
        machine1.status = "running"
        machine1.is_terminated.return_value = False

        machine2 = Mock()
        machine2.machine_id = "machine-002"
        machine2.return_request_id = "ret-00000000-0000-0000-0000-000000000123"
        machine2.status = "running"
        machine2.is_terminated.return_value = False

        machine3 = Mock()
        machine3.machine_id = "machine-003"
        machine3.return_request_id = None  # Valid
        machine3.status = "running"
        machine3.is_terminated.return_value = False

        # Mock repository responses
        def mock_get_by_id(machine_id):
            key = getattr(machine_id, "value", machine_id)
            if key == "machine-001":
                return machine1
            elif key == "machine-002":
                return machine2
            elif key == "machine-003":
                return machine3
            elif key == "ret-00000000-0000-0000-0000-000000000123":
                active_request = Mock()
                active_request.status = RequestStatus.IN_PROGRESS
                return active_request
            return None

        self.mock_uow.machines.get_by_id.side_effect = mock_get_by_id
        self.mock_uow.requests.get_by_id.side_effect = mock_get_by_id

        # Create command with all machines (multiple machines)
        command = CreateReturnRequestCommand(
            machine_ids=["machine-001", "machine-002", "machine-003"]
        )

        # Validation should pass (no exception for multiple machines)
        await self.handler.validate_command(command)

        result = self.handler._validate_and_filter_machines(command.machine_ids)

        # Verify filtering worked correctly
        assert result["valid_machines"] == ["machine-001", "machine-003"]

        assert len(result["skipped_machines"]) == 1
        assert result["skipped_machines"][0]["machine_id"] == "machine-002"
        assert "pending return request" in result["skipped_machines"][0]["reason"]

    @pytest.mark.asyncio
    async def test_single_machine_return_still_validates_properly(self):
        """Test that single machine return still validates properly (should still raise for invalid)."""
        # For single machine operations, we should still validate strictly
        machine = Mock()
        machine.machine_id = "machine-001"
        machine.return_request_id = "ret-00000000-0000-0000-0000-000000000123"
        machine.status = "running"
        machine.is_terminated.return_value = False

        active_request = Mock()
        active_request.status = RequestStatus.IN_PROGRESS

        self.mock_uow.machines.get_by_id.return_value = machine
        self.mock_uow.requests.get_by_id.return_value = active_request

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
        machine1.return_request_id = "ret-00000000-0000-0000-0000-000000000123"
        machine1.status = "running"
        machine1.is_terminated.return_value = False

        machine2 = Mock()
        machine2.machine_id = "machine-002"
        machine2.return_request_id = "ret-00000000-0000-0000-0000-000000000456"
        machine2.status = "running"
        machine2.is_terminated.return_value = False

        def mock_get_by_id(machine_id):
            key = getattr(machine_id, "value", machine_id)
            if key == "machine-001":
                return machine1
            elif key == "machine-002":
                return machine2
            elif key in {
                "ret-00000000-0000-0000-0000-000000000123",
                "ret-00000000-0000-0000-0000-000000000456",
            }:
                active_request = Mock()
                active_request.status = RequestStatus.IN_PROGRESS
                return active_request
            return None

        self.mock_uow.machines.get_by_id.side_effect = mock_get_by_id
        self.mock_uow.requests.get_by_id.side_effect = mock_get_by_id

        command = CreateReturnRequestCommand(machine_ids=["machine-001", "machine-002"])

        # Validation should pass (no exception for multiple machines)
        await self.handler.validate_command(command)

        result = self.handler._validate_and_filter_machines(command.machine_ids)

        # All machines should be filtered out
        assert result["valid_machines"] == []
        assert len(result["skipped_machines"]) == 2

    @pytest.mark.asyncio
    async def test_terminal_return_request_is_cleared_and_not_treated_as_pending(self):
        machine = Mock()
        machine.machine_id = "machine-001"
        machine.return_request_id = "ret-00000000-0000-0000-0000-000000000999"
        machine.status = "running"
        machine.is_terminated.return_value = False
        machine.model_copy.return_value = Mock()

        completed_request = Mock()
        completed_request.status = RequestStatus.COMPLETED

        def mock_get_by_id(machine_id):
            key = getattr(machine_id, "value", machine_id)
            if key == "machine-001":
                return machine
            if key == "ret-00000000-0000-0000-0000-000000000999":
                return completed_request
            return None

        self.mock_uow.machines.get_by_id.side_effect = mock_get_by_id
        self.mock_uow.requests.get_by_id.side_effect = mock_get_by_id

        command = CreateReturnRequestCommand(machine_ids=["machine-001"])

        await self.handler.validate_command(command)
        self.mock_uow.machines.save.assert_called_once()

    def test_update_machines_to_pending_clears_return_request_id(self):
        machine = Mock()
        machine.machine_id = "machine-001"
        machine.return_request_id = "ret-123"

        shutting_down_machine = Mock()
        cleared_machine = Mock()
        shutting_down_machine.model_copy.return_value = cleared_machine
        machine.update_status.return_value = shutting_down_machine

        self.mock_uow.machines.get_by_id.return_value = machine

        self.handler._update_machines_to_pending(["machine-001"])

        machine.update_status.assert_called_once()
        shutting_down_machine.model_copy.assert_called_once_with(update={"return_request_id": None})
        self.mock_uow.machines.save.assert_called_once_with(cleared_machine)

    @pytest.mark.asyncio
    async def test_successful_deprovisioning_persists_followup_context_and_stays_in_progress(self):
        request = Mock()
        request.request_id = "ret-001"
        request.provider_data = {}

        updated_request = Mock()
        request.set_provider_data.return_value = updated_request
        self.mock_uow.requests.save.return_value = []

        command_bus = Mock()
        command_bus.execute = AsyncMock()
        self.mock_container.get.return_value = command_bus

        self.handler._deprovisioning_orchestrator.execute_deprovisioning = AsyncMock(
            return_value={
                "success": True,
                "provider_data": {
                    "termination_requests": [
                        {
                            "pending_reconciliation": {
                                "resource_group": "test-rg",
                                "vmss_name": "vmss-demo",
                                "machine_ids": ["machine-001"],
                                "target_capacity": 0,
                                "orchestration_mode": "Flexible",
                                "delete_vmss_when_empty": True,
                            }
                        }
                    ]
                },
            }
        )
        self.handler._machine_grouping_service.group_by_resource = Mock(return_value={})
        self.handler._update_machines_to_pending = Mock()

        await self.handler._execute_deprovisioning_for_request(
            ["machine-001"], request, "azure-default"
        )

        request.set_provider_data.assert_called_once()
        persisted_provider_data = request.set_provider_data.call_args.args[0]
        assert persisted_provider_data["follow_up_context"]["termination_requests"][0][
            "pending_reconciliation"
        ]["vmss_name"] == "vmss-demo"
        saved_request = self.mock_uow.requests.save.call_args[0][0]
        assert saved_request is updated_request
        statuses = [call.args[0].status for call in command_bus.execute.await_args_list]
        assert statuses == [RequestStatus.IN_PROGRESS, RequestStatus.IN_PROGRESS]
        messages = [call.args[0].message for call in command_bus.execute.await_args_list]
        assert messages[-1] == "Termination initiated, waiting for provider confirmation"


if __name__ == "__main__":
    pytest.main([__file__])
