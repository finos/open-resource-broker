"""Unit tests for machine_command_handlers.

Covers all handler functions with happy path and error/branch paths.
Uses a DI dispatch table pattern matching the existing house style.
"""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.orchestration.dtos import (
    GetMachineOutput,
    ListMachinesOutput,
    StartMachinesOutput,
    StopMachinesOutput,
)
from orb.interface.machine_command_handlers import (
    handle_get_machine,
    handle_get_machine_status,
    handle_get_multiple_machines,
    handle_list_machines,
    handle_start_machines,
    handle_stop_machines,
)
from orb.interface.response_formatting_service import ResponseFormattingService


def _make_formatter() -> MagicMock:
    fmt = MagicMock(spec=ResponseFormattingService)
    fmt.format_machine_list.return_value = InterfaceResponse(data={"machines": []})
    fmt.format_success.return_value = InterfaceResponse(data={"ok": True})
    fmt.format_error.return_value = InterfaceResponse(data={"error": "err"}, exit_code=1)
    fmt.format_machine_operation.return_value = InterfaceResponse(data={"machine": {}})
    return fmt


@pytest.mark.unit
class TestHandleGetMachineStatus:
    """Tests for handle_get_machine_status."""

    @pytest.mark.asyncio
    async def test_all_flag_calls_list_orchestrator(self):
        from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator

        fmt = _make_formatter()
        fmt.format_machine_list.return_value = InterfaceResponse(data={"machines": ["m1"]})
        list_orch = AsyncMock(spec=ListMachinesOrchestrator)
        list_orch.execute.return_value = ListMachinesOutput(machines=[], count=0, total_count=0)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ListMachinesOrchestrator: list_orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            all=True,
            machine_ids=None,
            machine_ids_flag=None,
            status=None,
            provider_name=None,
            provider_type=None,
            request_id=None,
            limit=None,
            offset=None,
            timestamp_format=None,
            filter=None,
        )
        result = await handle_get_machine_status(args)

        list_orch.execute.assert_awaited_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_both_all_and_specific_ids_returns_error(self):
        fmt = _make_formatter()
        container = MagicMock()
        container.get.return_value = fmt

        args = Namespace(
            _container=container,
            all=True,
            machine_ids=["m1"],
            machine_ids_flag=None,
        )
        result = await handle_get_machine_status(args)

        assert result.exit_code == 1
        assert "Cannot use --all" in result.data.get("error", "")

    @pytest.mark.asyncio
    async def test_no_ids_and_no_all_returns_error(self):
        fmt = _make_formatter()
        container = MagicMock()
        container.get.return_value = fmt

        args = Namespace(
            _container=container,
            all=False,
            machine_ids=None,
            machine_ids_flag=None,
        )
        result = await handle_get_machine_status(args)

        assert result.exit_code == 1
        assert "No machine IDs" in result.data.get("error", "")

    @pytest.mark.asyncio
    async def test_specific_ids_calls_get_orchestrator(self):
        from orb.application.services.orchestration.get_machine import GetMachineOrchestrator

        fmt = _make_formatter()
        get_orch = AsyncMock(spec=GetMachineOrchestrator)
        mock_machine = MagicMock()
        get_orch.execute.return_value = GetMachineOutput(machine=mock_machine)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            GetMachineOrchestrator: get_orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            all=False,
            machine_ids=["m-aaa"],
            machine_ids_flag=None,
        )
        result = await handle_get_machine_status(args)

        get_orch.execute.assert_awaited()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_machine_ids_flag_used_when_present(self):
        """machine_ids_flag is also collected into the IDs list."""
        from orb.application.services.orchestration.get_machine import GetMachineOrchestrator

        fmt = _make_formatter()
        get_orch = AsyncMock(spec=GetMachineOrchestrator)
        get_orch.execute.return_value = GetMachineOutput(machine=MagicMock())

        container = MagicMock()
        container.get.side_effect = lambda t: {
            GetMachineOrchestrator: get_orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            all=False,
            machine_ids=None,
            machine_ids_flag=["m-flag"],
        )
        result = await handle_get_machine_status(args)

        get_orch.execute.assert_awaited_once()
        assert isinstance(result, InterfaceResponse)


@pytest.mark.unit
class TestHandleListMachines:
    """Tests for handle_list_machines."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_interface_response(self):
        from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=ListMachinesOrchestrator)
        orch.execute.return_value = ListMachinesOutput(machines=[], count=0, total_count=0)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ListMachinesOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            status=None,
            provider_name=None,
            provider_type=None,
            request_id=None,
            limit=None,
            offset=None,
            timestamp_format=None,
            filter=None,
        )
        result = await handle_list_machines(args)

        orch.execute.assert_awaited_once()
        fmt.format_machine_list.assert_called_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_limit_and_offset_defaults(self):
        """When limit/offset are None the handler must default to 100/0."""
        from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=ListMachinesOrchestrator)
        orch.execute.return_value = ListMachinesOutput(machines=[], count=0, total_count=0)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ListMachinesOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            status=None,
            provider_name=None,
            provider_type=None,
            request_id=None,
            limit=None,
            offset=None,
            timestamp_format=None,
            filter=None,
        )
        await handle_list_machines(args)

        call_input = orch.execute.call_args[0][0]
        assert call_input.limit == 100
        assert call_input.offset == 0

    @pytest.mark.asyncio
    async def test_explicit_limit_and_offset_forwarded(self):
        from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=ListMachinesOrchestrator)
        orch.execute.return_value = ListMachinesOutput(machines=[], count=0, total_count=0)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ListMachinesOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            status="active",
            provider_name=None,
            provider_type=None,
            request_id=None,
            limit=5,
            offset=10,
            timestamp_format=None,
            filter=None,
        )
        await handle_list_machines(args)

        call_input = orch.execute.call_args[0][0]
        assert call_input.limit == 5
        assert call_input.offset == 10


@pytest.mark.unit
class TestHandleStopMachines:
    """Tests for handle_stop_machines."""

    @pytest.mark.asyncio
    async def test_all_without_force_returns_error(self):
        container = MagicMock()
        args = Namespace(
            _container=container,
            all=True,
            force=False,
            machine_ids=None,
            machine_ids_flag=None,
        )
        result = await handle_stop_machines(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1
        assert "--all without --force" in result.data.get("error", "")

    @pytest.mark.asyncio
    async def test_all_and_specific_ids_returns_error(self):
        container = MagicMock()
        args = Namespace(
            _container=container,
            all=True,
            force=True,
            machine_ids=["m1"],
            machine_ids_flag=None,
        )
        result = await handle_stop_machines(args)

        assert result.exit_code == 1
        assert "Cannot use --all" in result.data.get("error", "")

    @pytest.mark.asyncio
    async def test_no_ids_no_all_returns_error(self):
        container = MagicMock()
        args = Namespace(
            _container=container,
            all=False,
            force=False,
            machine_ids=None,
            machine_ids_flag=None,
        )
        result = await handle_stop_machines(args)

        assert result.exit_code == 1
        assert "No machines specified" in result.data.get("error", "")

    @pytest.mark.asyncio
    async def test_happy_path_specific_ids(self):
        from orb.application.services.orchestration.stop_machines import StopMachinesOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=StopMachinesOrchestrator)
        orch.execute.return_value = StopMachinesOutput(
            message="stopped",
            stopped_machines=["m1"],
            failed_machines=[],
        )

        container = MagicMock()
        container.get.side_effect = lambda t: {
            StopMachinesOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            all=False,
            force=False,
            machine_ids=["m1"],
            machine_ids_flag=None,
            provider_name=None,
            provider_type=None,
            filter=None,
        )
        result = await handle_stop_machines(args)

        orch.execute.assert_awaited_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_all_with_force_calls_orchestrator(self):
        from orb.application.services.orchestration.stop_machines import StopMachinesOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=StopMachinesOrchestrator)
        orch.execute.return_value = StopMachinesOutput(
            message="all stopped", stopped_machines=[], failed_machines=[]
        )

        container = MagicMock()
        container.get.side_effect = lambda t: {
            StopMachinesOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            all=True,
            force=True,
            machine_ids=None,
            machine_ids_flag=None,
            provider_name=None,
            provider_type=None,
            filter=None,
        )
        result = await handle_stop_machines(args)

        orch.execute.assert_awaited_once()
        call_input = orch.execute.call_args[0][0]
        assert call_input.all_machines is True
        assert call_input.force is True
        assert isinstance(result, InterfaceResponse)


@pytest.mark.unit
class TestHandleStartMachines:
    """Tests for handle_start_machines."""

    @pytest.mark.asyncio
    async def test_all_and_specific_ids_returns_error(self):
        container = MagicMock()
        args = Namespace(
            _container=container,
            all=True,
            machine_ids=["m1"],
            machine_ids_flag=None,
        )
        result = await handle_start_machines(args)

        assert result.exit_code == 1
        assert "Cannot use --all" in result.data.get("error", "")

    @pytest.mark.asyncio
    async def test_no_ids_no_all_returns_error(self):
        container = MagicMock()
        args = Namespace(
            _container=container,
            all=False,
            machine_ids=None,
            machine_ids_flag=None,
        )
        result = await handle_start_machines(args)

        assert result.exit_code == 1
        assert "No machines specified" in result.data.get("error", "")

    @pytest.mark.asyncio
    async def test_happy_path_specific_ids(self):
        from orb.application.services.orchestration.start_machines import StartMachinesOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=StartMachinesOrchestrator)
        orch.execute.return_value = StartMachinesOutput(
            message="started", started_machines=["m1"], failed_machines=[]
        )

        container = MagicMock()
        container.get.side_effect = lambda t: {
            StartMachinesOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            all=False,
            machine_ids=["m1"],
            machine_ids_flag=None,
            provider_name=None,
            provider_type=None,
            filter=None,
        )
        result = await handle_start_machines(args)

        orch.execute.assert_awaited_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_all_flag_forwards_all_machines(self):
        from orb.application.services.orchestration.start_machines import StartMachinesOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=StartMachinesOrchestrator)
        orch.execute.return_value = StartMachinesOutput(
            message="ok", started_machines=[], failed_machines=[]
        )

        container = MagicMock()
        container.get.side_effect = lambda t: {
            StartMachinesOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            all=True,
            machine_ids=None,
            machine_ids_flag=None,
            provider_name=None,
            provider_type=None,
            filter=None,
        )
        await handle_start_machines(args)

        call_input = orch.execute.call_args[0][0]
        assert call_input.all_machines is True


@pytest.mark.unit
class TestHandleGetMachine:
    """Tests for handle_get_machine (single machine show)."""

    @pytest.mark.asyncio
    async def test_returns_error_when_no_machine_id(self):
        fmt = _make_formatter()
        container = MagicMock()
        container.get.side_effect = lambda t: {
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(_container=container, machine_id=None, flag_machine_id=None)
        result = await handle_get_machine(args)

        fmt.format_error.assert_called_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_returns_error_when_machine_not_found(self):
        from orb.application.services.orchestration.get_machine import GetMachineOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=GetMachineOrchestrator)
        orch.execute.return_value = GetMachineOutput(machine=None)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            GetMachineOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(_container=container, machine_id="m-missing", flag_machine_id=None)
        result = await handle_get_machine(args)

        fmt.format_error.assert_called_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_happy_path_returns_machine_operation(self):
        from orb.application.services.orchestration.get_machine import GetMachineOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=GetMachineOrchestrator)
        mock_machine = MagicMock()
        mock_machine.model_dump.return_value = {"machine_id": "m-123"}
        orch.execute.return_value = GetMachineOutput(machine=mock_machine)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            GetMachineOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(_container=container, machine_id="m-123", flag_machine_id=None)
        result = await handle_get_machine(args)

        fmt.format_machine_operation.assert_called_once_with({"machine_id": "m-123"})
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_flag_machine_id_used_as_fallback(self):
        from orb.application.services.orchestration.get_machine import GetMachineOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=GetMachineOrchestrator)
        mock_machine = MagicMock()
        mock_machine.model_dump.return_value = {"machine_id": "m-flag"}
        orch.execute.return_value = GetMachineOutput(machine=mock_machine)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            GetMachineOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(_container=container, machine_id=None, flag_machine_id="m-flag")
        result = await handle_get_machine(args)

        orch.execute.assert_awaited_once()
        call_input = orch.execute.call_args[0][0]
        assert call_input.machine_id == "m-flag"
        assert isinstance(result, InterfaceResponse)


@pytest.mark.unit
class TestHandleGetMultipleMachines:
    """Tests for handle_get_multiple_machines."""

    @pytest.mark.asyncio
    async def test_no_ids_returns_error_dict(self):
        container = MagicMock()
        args = Namespace(
            _container=container,
            machine_ids=None,
            flag_machine_ids=None,
            flag_ids=None,
        )
        result = await handle_get_multiple_machines(args)

        assert isinstance(result, dict)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_happy_path_via_query_bus(self):
        from orb.infrastructure.di.buses import QueryBus

        mock_bus = AsyncMock(spec=QueryBus)
        mock_result = MagicMock()
        mock_result.machines = []
        mock_result.found_count = 0
        mock_result.not_found_ids = []
        mock_result.total_requested = 2
        mock_bus.execute.return_value = mock_result

        container = MagicMock()
        container.get.side_effect = lambda t: {
            QueryBus: mock_bus,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            machine_ids=["m1", "m2"],
            flag_machine_ids=None,
            flag_ids=None,
            include_requests=True,
        )
        result = await handle_get_multiple_machines(args)

        mock_bus.execute.assert_awaited_once()
        assert result["total_requested"] == 2
        assert "machines" in result

    @pytest.mark.asyncio
    async def test_flag_ids_merged(self):
        from orb.infrastructure.di.buses import QueryBus

        mock_bus = AsyncMock(spec=QueryBus)
        mock_result = MagicMock()
        mock_result.machines = []
        mock_result.found_count = 0
        mock_result.not_found_ids = []
        mock_result.total_requested = 1
        mock_bus.execute.return_value = mock_result

        container = MagicMock()
        container.get.side_effect = lambda t: {QueryBus: mock_bus}.get(t, MagicMock())

        args = Namespace(
            _container=container,
            machine_ids=None,
            flag_machine_ids=None,
            flag_ids=["m-via-flag"],
            include_requests=False,
        )
        result = await handle_get_multiple_machines(args)

        query_arg = mock_bus.execute.call_args[0][0]
        assert "m-via-flag" in query_arg.machine_ids
        assert "machines" in result
