"""Unit tests for StopMachinesOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.machine.commands import UpdateMachineStatusCommand
from orb.application.machine.dto import MachineDTO
from orb.application.provider.commands import ExecuteProviderOperationCommand
from orb.application.services.orchestration.dtos import StopMachinesInput, StopMachinesOutput
from orb.application.services.orchestration.stop_machines import StopMachinesOrchestrator
from orb.providers.base.strategy import ProviderOperationType


def make_machine_dto(machine_id: str) -> MagicMock:
    m = MagicMock(spec=MachineDTO)
    m.machine_id = machine_id
    return m


def make_command_bus_side_effect(results: dict[str, bool]) -> AsyncMock:
    """Return an AsyncMock that sets command.result for ExecuteProviderOperationCommand."""

    async def _side_effect(cmd):
        if isinstance(cmd, ExecuteProviderOperationCommand):
            cmd.result = {
                "success": True,
                "data": {"results": results},
                "error_message": None,
            }

    return AsyncMock(side_effect=_side_effect)


@pytest.fixture
def mock_command_bus():
    return MagicMock()


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
    return StopMachinesOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestStopMachinesOrchestrator:
    @pytest.mark.asyncio
    async def test_happy_path_specific_ids(self, orchestrator, mock_command_bus):
        mock_command_bus.execute = make_command_bus_side_effect({"m-001": True, "m-002": True})
        result = await orchestrator.execute(StopMachinesInput(machine_ids=["m-001", "m-002"]))
        assert isinstance(result, StopMachinesOutput)
        assert sorted(result.stopped_machines) == ["m-001", "m-002"]
        assert result.failed_machines == []
        assert result.success is True

    @pytest.mark.asyncio
    async def test_happy_path_all_machines(self, orchestrator, mock_command_bus, mock_query_bus):
        from orb.application.dto.queries import ListMachinesQuery

        mock_query_bus.execute.return_value = [
            make_machine_dto("m-001"),
            make_machine_dto("m-002"),
        ]
        mock_command_bus.execute = make_command_bus_side_effect({"m-001": True, "m-002": True})

        result = await orchestrator.execute(StopMachinesInput(all_machines=True, force=True))

        mock_query_bus.execute.assert_called_once()
        query_arg = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query_arg, ListMachinesQuery)
        assert query_arg.status == "running"
        assert sorted(result.stopped_machines) == ["m-001", "m-002"]

    @pytest.mark.asyncio
    async def test_no_machines_to_stop_returns_early(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        mock_command_bus.execute = AsyncMock()

        result = await orchestrator.execute(StopMachinesInput(all_machines=True, force=True))

        mock_command_bus.execute.assert_not_called()
        assert result.success is True
        assert result.stopped_machines == []
        assert result.message == "No machines to stop"

    @pytest.mark.asyncio
    async def test_provider_partial_failure(self, orchestrator, mock_command_bus):
        mock_command_bus.execute = make_command_bus_side_effect({"m-001": True, "m-002": False})

        result = await orchestrator.execute(StopMachinesInput(machine_ids=["m-001", "m-002"]))

        assert result.stopped_machines == ["m-001"]
        assert result.failed_machines == ["m-002"]
        assert result.success is False

    @pytest.mark.asyncio
    async def test_provider_operation_fails_entirely(self, orchestrator, mock_command_bus):
        async def _fail(cmd):
            if isinstance(cmd, ExecuteProviderOperationCommand):
                cmd.result = {"success": False, "data": None, "error_message": "AWS error"}

        mock_command_bus.execute = AsyncMock(side_effect=_fail)

        result = await orchestrator.execute(StopMachinesInput(machine_ids=["m-001", "m-002"]))

        assert sorted(result.failed_machines) == ["m-001", "m-002"]
        assert result.success is False
        # UpdateMachineStatusCommand must not have been dispatched
        for c in mock_command_bus.execute.call_args_list:
            assert not isinstance(c[0][0], UpdateMachineStatusCommand)

    @pytest.mark.asyncio
    async def test_dispatches_execute_provider_operation_command(
        self, orchestrator, mock_command_bus
    ):
        mock_command_bus.execute = make_command_bus_side_effect({"m-001": True})

        await orchestrator.execute(StopMachinesInput(machine_ids=["m-001"]))

        first_call_cmd = mock_command_bus.execute.call_args_list[0][0][0]
        assert isinstance(first_call_cmd, ExecuteProviderOperationCommand)
        assert first_call_cmd.operation.operation_type == ProviderOperationType.STOP_INSTANCES
        assert first_call_cmd.operation.parameters == {"instance_ids": ["m-001"]}

    @pytest.mark.asyncio
    async def test_dispatches_update_machine_status_for_stopped(
        self, orchestrator, mock_command_bus
    ):
        mock_command_bus.execute = make_command_bus_side_effect({"m-001": True})

        await orchestrator.execute(StopMachinesInput(machine_ids=["m-001"]))

        all_cmds = [c[0][0] for c in mock_command_bus.execute.call_args_list]
        status_cmds = [c for c in all_cmds if isinstance(c, UpdateMachineStatusCommand)]
        assert len(status_cmds) == 1
        assert status_cmds[0].machine_id == "m-001"
        assert status_cmds[0].status == "stopping"

    @pytest.mark.asyncio
    async def test_machine_dto_attribute_access_not_dict(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        dto = MagicMock(spec=MachineDTO)
        dto.machine_id = "m-001"
        mock_query_bus.execute.return_value = [dto]
        mock_command_bus.execute = make_command_bus_side_effect({"m-001": True})

        # Should not raise TypeError
        result = await orchestrator.execute(StopMachinesInput(all_machines=True, force=True))
        assert result.stopped_machines == ["m-001"]

    @pytest.mark.asyncio
    async def test_command_bus_error_propagates(self, orchestrator, mock_command_bus):
        mock_command_bus.execute = AsyncMock(side_effect=RuntimeError("provider down"))

        with pytest.raises(RuntimeError, match="provider down"):
            await orchestrator.execute(StopMachinesInput(machine_ids=["m-001"]))

    def test_does_not_accept_provider_selection_port(self):
        import inspect

        sig = inspect.signature(StopMachinesOrchestrator.__init__)
        assert "provider_selection_port" not in sig.parameters
