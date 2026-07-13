"""Unit tests for StopMachinesOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import GetMachineQuery, ListMachinesQuery
from orb.application.machine.commands import (
    UpdateMachineProviderDataCommand,
    UpdateMachineStatusCommand,
)
from orb.application.machine.dto import MachineDTO
from orb.application.provider.commands import ExecuteProviderOperationCommand
from orb.application.services.orchestration.dtos import StopMachinesInput, StopMachinesOutput
from orb.application.services.orchestration.stop_machines import StopMachinesOrchestrator
from orb.providers.base.strategy import ProviderOperationType


def make_machine_dto(
    machine_id: str,
    provider_data: dict | None = None,
    provider_api: str = "some-api",
    resource_id: str = "res-001",
    request_id: str = "req-001",
) -> MagicMock:
    m = MagicMock(spec=MachineDTO)
    m.machine_id = machine_id
    m.provider_data = provider_data if provider_data is not None else {}
    m.provider_api = provider_api
    m.resource_id = resource_id
    m.request_id = request_id
    return m


def make_command_bus_side_effect(
    results: dict[str, bool],
    replicas_before_stop_per_machine: dict[str, int] | None = None,
) -> AsyncMock:
    """Return an AsyncMock that sets command.result for ExecuteProviderOperationCommand.

    Optionally include replicas_before_stop_per_machine in the result data to
    simulate the k8s provider returning pre-stop replica counts.
    """

    async def _side_effect(cmd):
        if isinstance(cmd, ExecuteProviderOperationCommand):
            data: dict = {"results": results}
            if replicas_before_stop_per_machine is not None:
                data["replicas_before_stop_per_machine"] = replicas_before_stop_per_machine
            cmd.result = {
                "success": True,
                "data": data,
                "error_message": None,
            }

    return AsyncMock(side_effect=_side_effect)


def make_query_bus_for_specific_ids(
    machine_dtos: dict[str, MagicMock],
) -> AsyncMock:
    """Return an AsyncMock query_bus that serves GetMachineQuery per machine_id."""

    async def _side_effect(query):
        if isinstance(query, GetMachineQuery):
            return machine_dtos.get(query.machine_id)
        return None

    return AsyncMock(side_effect=_side_effect)


def make_query_bus_for_all_machines(
    list_result: list,
    machine_dtos: dict[str, MagicMock],
) -> AsyncMock:
    """Return an AsyncMock query_bus that returns list_result for ListMachinesQuery
    and individual DTOs for GetMachineQuery calls."""

    async def _side_effect(query):
        if isinstance(query, ListMachinesQuery):
            return list_result
        if isinstance(query, GetMachineQuery):
            return machine_dtos.get(query.machine_id)
        return None

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
def mock_provider_registry_service():
    from orb.application.services.provider_registry_service import ProviderRegistryService
    from orb.domain.base.results import ProviderSelectionResult

    svc = MagicMock(spec=ProviderRegistryService)
    svc.select_active_provider.return_value = ProviderSelectionResult(
        provider_type="aws",
        provider_name="aws-default",
        selection_reason="test_fixture",
        confidence=1.0,
    )
    return svc


@pytest.fixture
def orchestrator(mock_command_bus, mock_query_bus, mock_logger, mock_provider_registry_service):
    return StopMachinesOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
        provider_registry_service=mock_provider_registry_service,
    )


@pytest.mark.unit
@pytest.mark.application
class TestStopMachinesOrchestrator:
    @pytest.mark.asyncio
    async def test_happy_path_specific_ids(self, orchestrator, mock_command_bus, mock_query_bus):
        dto_m001 = make_machine_dto("m-001")
        dto_m002 = make_machine_dto("m-002")
        mock_query_bus.execute = make_query_bus_for_specific_ids(
            {"m-001": dto_m001, "m-002": dto_m002}
        )
        mock_command_bus.execute = make_command_bus_side_effect({"m-001": True, "m-002": True})
        result = await orchestrator.execute(StopMachinesInput(machine_ids=["m-001", "m-002"]))
        assert isinstance(result, StopMachinesOutput)
        assert sorted(result.stopped_machines) == ["m-001", "m-002"]
        assert result.failed_machines == []
        assert result.success is True

    @pytest.mark.asyncio
    async def test_happy_path_all_machines(self, orchestrator, mock_command_bus, mock_query_bus):
        dto_m001 = make_machine_dto("m-001")
        dto_m002 = make_machine_dto("m-002")
        mock_query_bus.execute = make_query_bus_for_all_machines(
            list_result=[dto_m001, dto_m002],
            machine_dtos={"m-001": dto_m001, "m-002": dto_m002},
        )
        mock_command_bus.execute = make_command_bus_side_effect({"m-001": True, "m-002": True})

        result = await orchestrator.execute(StopMachinesInput(all_machines=True, force=True))

        # The query_bus must have been called at least three times:
        # once with ListMachinesQuery and once per machine with GetMachineQuery.
        query_calls = mock_query_bus.execute.call_args_list
        query_types = [type(c[0][0]) for c in query_calls]
        assert ListMachinesQuery in query_types, "Expected a ListMachinesQuery call"
        get_machine_calls = [c for c in query_calls if isinstance(c[0][0], GetMachineQuery)]
        assert len(get_machine_calls) == 2, "Expected one GetMachineQuery per machine"
        fetched_ids = {c[0][0].machine_id for c in get_machine_calls}
        assert fetched_ids == {"m-001", "m-002"}
        list_query_arg = next(
            c[0][0] for c in query_calls if isinstance(c[0][0], ListMachinesQuery)
        )
        assert list_query_arg.status == "running"
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
    async def test_provider_partial_failure(self, orchestrator, mock_command_bus, mock_query_bus):
        dto_m001 = make_machine_dto("m-001")
        dto_m002 = make_machine_dto("m-002")
        mock_query_bus.execute = make_query_bus_for_specific_ids(
            {"m-001": dto_m001, "m-002": dto_m002}
        )
        mock_command_bus.execute = make_command_bus_side_effect({"m-001": True, "m-002": False})

        result = await orchestrator.execute(StopMachinesInput(machine_ids=["m-001", "m-002"]))

        assert result.stopped_machines == ["m-001"]
        assert result.failed_machines == ["m-002"]
        assert result.success is False

    @pytest.mark.asyncio
    async def test_provider_operation_fails_entirely(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        dto_m001 = make_machine_dto("m-001")
        dto_m002 = make_machine_dto("m-002")
        mock_query_bus.execute = make_query_bus_for_specific_ids(
            {"m-001": dto_m001, "m-002": dto_m002}
        )

        async def _fail(cmd):
            if isinstance(cmd, ExecuteProviderOperationCommand):
                cmd.result = {"success": False, "data": None, "error_message": "AWS error"}

        mock_command_bus.execute = AsyncMock(side_effect=_fail)

        result = await orchestrator.execute(StopMachinesInput(machine_ids=["m-001", "m-002"]))

        assert sorted(result.failed_machines) == ["m-001", "m-002"]
        assert result.success is False
        # UpdateMachineStatusCommand must not have been dispatched on failure
        for c in mock_command_bus.execute.call_args_list:
            assert not isinstance(c[0][0], UpdateMachineStatusCommand)

    @pytest.mark.asyncio
    async def test_dispatches_execute_provider_operation_command(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        """The ExecuteProviderOperationCommand must carry instance_ids AND
        machine_coordinates so provider strategies (e.g. k8s) can resolve
        the workload controller instead of using bare pod names."""
        dto_m001 = make_machine_dto(
            "m-001",
            provider_data={"controller": "deploy/worker"},
            provider_api="apps/v1",
            resource_id="deploy/worker",
            request_id="req-abc",
        )
        mock_query_bus.execute = make_query_bus_for_specific_ids({"m-001": dto_m001})
        mock_command_bus.execute = make_command_bus_side_effect({"m-001": True})

        await orchestrator.execute(StopMachinesInput(machine_ids=["m-001"]))

        all_cmds = [c[0][0] for c in mock_command_bus.execute.call_args_list]
        exec_cmds = [c for c in all_cmds if isinstance(c, ExecuteProviderOperationCommand)]
        assert len(exec_cmds) == 1
        op = exec_cmds[0].operation
        assert op.operation_type == ProviderOperationType.STOP_INSTANCES
        # instance_ids must still be present for backward-compatible providers
        assert op.parameters["instance_ids"] == ["m-001"]
        # machine_coordinates carries per-machine provider context
        coords = op.parameters["machine_coordinates"]
        assert "m-001" in coords
        assert coords["m-001"]["provider_data"] == {"controller": "deploy/worker"}
        assert coords["m-001"]["provider_api"] == "apps/v1"
        assert coords["m-001"]["resource_id"] == "deploy/worker"
        assert coords["m-001"]["request_id"] == "req-abc"

    @pytest.mark.asyncio
    async def test_dispatches_update_machine_status_for_stopped(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        dto_m001 = make_machine_dto("m-001")
        mock_query_bus.execute = make_query_bus_for_specific_ids({"m-001": dto_m001})
        mock_command_bus.execute = make_command_bus_side_effect({"m-001": True})

        await orchestrator.execute(StopMachinesInput(machine_ids=["m-001"]))

        all_cmds = [c[0][0] for c in mock_command_bus.execute.call_args_list]
        status_cmds = [c for c in all_cmds if isinstance(c, UpdateMachineStatusCommand)]
        assert len(status_cmds) == 1
        assert status_cmds[0].machine_id == "m-001"
        assert status_cmds[0].status == "stopping"

    @pytest.mark.asyncio
    async def test_persists_replicas_before_stop_when_provider_returns_count(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        """When the provider returns replicas_before_stop_per_machine, the
        orchestrator must persist that count via UpdateMachineProviderDataCommand
        so that start can restore the correct replica count even after a manual
        scale event between stop and start."""
        dto_m001 = make_machine_dto(
            "m-001",
            provider_data={"controller": "deploy/worker"},
            provider_api="apps/v1",
            resource_id="deploy/worker",
            request_id="req-abc",
        )
        mock_query_bus.execute = make_query_bus_for_specific_ids({"m-001": dto_m001})
        mock_command_bus.execute = make_command_bus_side_effect(
            results={"m-001": True},
            replicas_before_stop_per_machine={"m-001": 3},
        )

        result = await orchestrator.execute(StopMachinesInput(machine_ids=["m-001"]))

        assert result.stopped_machines == ["m-001"]
        assert result.success is True

        all_cmds = [c[0][0] for c in mock_command_bus.execute.call_args_list]
        pd_cmds = [c for c in all_cmds if isinstance(c, UpdateMachineProviderDataCommand)]
        assert len(pd_cmds) == 1, "Expected exactly one UpdateMachineProviderDataCommand"
        assert pd_cmds[0].machine_id == "m-001"
        assert pd_cmds[0].updates == {"replicas_before_stop": 3}

    @pytest.mark.asyncio
    async def test_no_replicas_persisted_when_provider_omits_count(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        """When the provider does not return replicas_before_stop_per_machine
        (e.g. AWS EC2), no UpdateMachineProviderDataCommand should be issued."""
        dto_m001 = make_machine_dto("m-001")
        mock_query_bus.execute = make_query_bus_for_specific_ids({"m-001": dto_m001})
        # No replicas_before_stop_per_machine in result
        mock_command_bus.execute = make_command_bus_side_effect({"m-001": True})

        await orchestrator.execute(StopMachinesInput(machine_ids=["m-001"]))

        all_cmds = [c[0][0] for c in mock_command_bus.execute.call_args_list]
        pd_cmds = [c for c in all_cmds if isinstance(c, UpdateMachineProviderDataCommand)]
        assert len(pd_cmds) == 0, (
            "No UpdateMachineProviderDataCommand expected for non-k8s provider"
        )

    @pytest.mark.asyncio
    async def test_machine_dto_attribute_access_not_dict(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        dto = make_machine_dto("m-001")
        mock_query_bus.execute = make_query_bus_for_all_machines(
            list_result=[dto],
            machine_dtos={"m-001": dto},
        )
        mock_command_bus.execute = make_command_bus_side_effect({"m-001": True})

        result = await orchestrator.execute(StopMachinesInput(all_machines=True, force=True))
        assert result.stopped_machines == ["m-001"]

    @pytest.mark.asyncio
    async def test_command_bus_error_propagates(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        dto_m001 = make_machine_dto("m-001")
        mock_query_bus.execute = make_query_bus_for_specific_ids({"m-001": dto_m001})
        mock_command_bus.execute = AsyncMock(side_effect=RuntimeError("provider down"))

        with pytest.raises(RuntimeError, match="provider down"):
            await orchestrator.execute(StopMachinesInput(machine_ids=["m-001"]))

    def test_does_not_accept_provider_selection_port(self):
        import inspect

        sig = inspect.signature(StopMachinesOrchestrator.__init__)
        assert "provider_selection_port" not in sig.parameters
