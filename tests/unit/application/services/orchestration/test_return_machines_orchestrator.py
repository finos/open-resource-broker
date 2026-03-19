"""Unit tests for ReturnMachinesOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.commands import CreateReturnRequestCommand
from orb.application.dto.queries import ListMachinesQuery
from orb.application.services.orchestration.dtos import ReturnMachinesInput, ReturnMachinesOutput
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator


@pytest.fixture
def mock_command_bus():
    bus = MagicMock()

    async def _set_request_ids(cmd):
        cmd.created_request_ids = ["ret-req-001"]

    bus.execute = AsyncMock(side_effect=_set_request_ids)
    return bus


@pytest.fixture
def mock_query_bus():
    bus = MagicMock()
    bus.execute = AsyncMock()
    return bus


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def orchestrator(mock_command_bus, mock_query_bus, mock_logger):
    return ReturnMachinesOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestReturnMachinesOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_create_return_request_command(
        self, orchestrator, mock_command_bus
    ):
        input = ReturnMachinesInput(machine_ids=["m-001", "m-002"])
        await orchestrator.execute(input)
        mock_command_bus.execute.assert_called_once()
        cmd = mock_command_bus.execute.call_args[0][0]
        assert isinstance(cmd, CreateReturnRequestCommand)
        assert cmd.machine_ids == ["m-001", "m-002"]

    @pytest.mark.asyncio
    async def test_execute_passes_force_flag(self, orchestrator, mock_command_bus):
        input = ReturnMachinesInput(machine_ids=["m-001"], force=True)
        await orchestrator.execute(input)
        cmd = mock_command_bus.execute.call_args[0][0]
        assert cmd.force_return is True

    @pytest.mark.asyncio
    async def test_execute_returns_pending_status(self, orchestrator):
        input = ReturnMachinesInput(machine_ids=["m-001"])
        result = await orchestrator.execute(input)
        assert isinstance(result, ReturnMachinesOutput)
        assert result.status == "pending"

    @pytest.mark.asyncio
    async def test_execute_returns_request_id_from_command(self, orchestrator, mock_command_bus):
        async def set_request_ids(cmd):
            cmd.created_request_ids = ["ret-req-001"]

        mock_command_bus.execute.side_effect = set_request_ids
        input = ReturnMachinesInput(machine_ids=["m-001"])
        result = await orchestrator.execute(input)
        assert result.request_id == "ret-req-001"

    @pytest.mark.asyncio
    async def test_execute_no_created_request_ids_returns_no_op(
        self, orchestrator, mock_command_bus
    ):
        async def set_empty(cmd):
            cmd.created_request_ids = []
            cmd.skipped_machines = ["m-001"]

        mock_command_bus.execute.side_effect = set_empty
        input = ReturnMachinesInput(machine_ids=["m-001"])
        result = await orchestrator.execute(input)
        assert result.status == "no_op"
        assert result.request_id is None
        assert result.skipped_machines == ["m-001"]

    @pytest.mark.asyncio
    async def test_execute_raw_contains_status(self, orchestrator):
        input = ReturnMachinesInput(machine_ids=["m-001"])
        result = await orchestrator.execute(input)
        assert result.status == "pending"

    @pytest.mark.asyncio
    async def test_execute_does_not_call_query_bus(self, orchestrator, mock_query_bus):
        input = ReturnMachinesInput(machine_ids=["m-001"])
        await orchestrator.execute(input)
        mock_query_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_command_bus_error_propagates(self, orchestrator, mock_command_bus):
        mock_command_bus.execute.side_effect = Exception("command failed")
        input = ReturnMachinesInput(machine_ids=["m-001"])
        with pytest.raises(Exception, match="command failed"):
            await orchestrator.execute(input)

    @pytest.mark.asyncio
    async def test_all_machines_dispatches_list_machines_query(
        self, orchestrator, mock_query_bus, mock_command_bus
    ):
        mock_query_bus.execute.return_value = [
            MagicMock(machine_id="m-001"),
            MagicMock(machine_id="m-002"),
        ]

        async def _set_request_ids(cmd):
            cmd.created_request_ids = ["ret-req-001"]

        mock_command_bus.execute.side_effect = _set_request_ids
        input = ReturnMachinesInput(all_machines=True, force=True)
        await orchestrator.execute(input)
        mock_query_bus.execute.assert_called_once()
        query_arg = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query_arg, ListMachinesQuery)
        assert query_arg.all_resources is True

    @pytest.mark.asyncio
    async def test_all_machines_passes_resolved_ids_to_command(
        self, orchestrator, mock_query_bus, mock_command_bus
    ):
        mock_query_bus.execute.return_value = [
            MagicMock(machine_id="m-001"),
            MagicMock(machine_id="m-002"),
        ]

        async def _set_request_ids(cmd):
            cmd.created_request_ids = ["ret-req-001"]

        mock_command_bus.execute.side_effect = _set_request_ids
        input = ReturnMachinesInput(all_machines=True, force=True)
        await orchestrator.execute(input)
        cmd = mock_command_bus.execute.call_args[0][0]
        assert cmd.machine_ids == ["m-001", "m-002"]

    @pytest.mark.asyncio
    async def test_all_machines_no_active_machines_returns_no_machines_status(
        self, orchestrator, mock_query_bus, mock_command_bus
    ):
        mock_query_bus.execute.return_value = []
        input = ReturnMachinesInput(all_machines=True, force=True)
        result = await orchestrator.execute(input)
        assert result.status == "no_machines"
        assert result.request_id is None
        mock_command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_machines_query_returns_none_treated_as_empty(
        self, orchestrator, mock_query_bus, mock_command_bus
    ):
        mock_query_bus.execute.return_value = None
        input = ReturnMachinesInput(all_machines=True, force=True)
        result = await orchestrator.execute(input)
        assert result.status == "no_machines"
        mock_command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_machines_returns_pending_status_when_machines_exist(
        self, orchestrator, mock_query_bus, mock_command_bus
    ):
        mock_query_bus.execute.return_value = [MagicMock(machine_id="m-001")]

        async def _set_request_ids(cmd):
            cmd.created_request_ids = ["ret-req-001"]

        mock_command_bus.execute.side_effect = _set_request_ids
        input = ReturnMachinesInput(all_machines=True, force=True)
        result = await orchestrator.execute(input)
        assert result.status == "pending"
        assert result.request_id == "ret-req-001"
