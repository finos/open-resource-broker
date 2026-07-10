"""Regression tests for Paginated-not-iterable bug in --all machine orchestrators.

The ListMachinesQuery handler returns Paginated[MachineDTO], not a plain list.
The three orchestrators that use --all (return, stop, start) must unpack .items
rather than iterating the Paginated wrapper directly.

Before the fix, iterating the Paginated wrapper raised:
    TypeError: 'Paginated' object is not iterable
which was then wrapped by the exception handler as:
    {"error": true, "message": "Type error: 'Paginated' object is not iterable", "type": "ValidationError"}
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.machine.dto import MachineDTO
from orb.application.services.orchestration.dtos import (
    Paginated,
    ReturnMachinesInput,
    StartMachinesInput,
    StopMachinesInput,
)
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
from orb.application.services.orchestration.start_machines import StartMachinesOrchestrator
from orb.application.services.orchestration.stop_machines import StopMachinesOrchestrator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_machine_dto(machine_id: str) -> MagicMock:
    m = MagicMock(spec=MachineDTO)
    m.machine_id = machine_id
    return m


def _make_paginated(*machine_ids: str) -> Paginated[MachineDTO]:
    """Build a real Paginated[MachineDTO] as the query handler produces it."""
    items = [_make_machine_dto(mid) for mid in machine_ids]
    return Paginated(items=items, total_count=len(items), total_unfiltered=len(items))


def _make_logger() -> MagicMock:
    from orb.domain.base.ports.logging_port import LoggingPort

    mock = MagicMock(spec=LoggingPort)
    mock.info = MagicMock()
    mock.debug = MagicMock()
    mock.error = MagicMock()
    mock.warning = MagicMock()
    return mock


def _make_provider_registry_service() -> MagicMock:
    from orb.application.services.provider_registry_service import ProviderRegistryService
    from orb.domain.base.results import ProviderSelectionResult

    svc = MagicMock(spec=ProviderRegistryService)
    svc.select_active_provider.return_value = ProviderSelectionResult(
        provider_type="k8s",
        provider_name="k8s-default",
        selection_reason="test",
        confidence=1.0,
    )
    return svc


# ---------------------------------------------------------------------------
# ReturnMachinesOrchestrator
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.application
class TestReturnMachinesPaginatedRegression:
    """Verify ReturnMachinesOrchestrator handles Paginated result from ListMachinesQuery."""

    @pytest.mark.asyncio
    async def test_all_machines_with_paginated_result_does_not_raise(self):
        """The --all path must not raise TypeError when query bus returns Paginated."""
        paginated = _make_paginated("m-001", "m-002")

        mock_query_bus = MagicMock()
        mock_query_bus.execute = AsyncMock(return_value=paginated)

        mock_command_bus = MagicMock()

        async def _set_request_ids(cmd):
            cmd.created_request_ids = ["ret-req-001"]

        mock_command_bus.execute = AsyncMock(side_effect=_set_request_ids)

        orchestrator = ReturnMachinesOrchestrator(
            command_bus=mock_command_bus,
            query_bus=mock_query_bus,
            logger=_make_logger(),
        )

        # Must not raise — previously raised TypeError: 'Paginated' object is not iterable
        result = await orchestrator.execute(ReturnMachinesInput(all_machines=True, force=True))

        assert result.status == "pending"
        assert result.request_id == "ret-req-001"
        assert sorted(result.machine_ids) == ["m-001", "m-002"]

    @pytest.mark.asyncio
    async def test_all_machines_paginated_empty_items_returns_no_machines(self):
        """An empty Paginated result must produce status='no_machines', not iterate."""
        paginated = _make_paginated()  # 0 items

        mock_query_bus = MagicMock()
        mock_query_bus.execute = AsyncMock(return_value=paginated)
        mock_command_bus = MagicMock()
        mock_command_bus.execute = AsyncMock()

        orchestrator = ReturnMachinesOrchestrator(
            command_bus=mock_command_bus,
            query_bus=mock_query_bus,
            logger=_make_logger(),
        )

        result = await orchestrator.execute(ReturnMachinesInput(all_machines=True, force=True))

        assert result.status == "no_machines"
        mock_command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_machines_paginated_ids_forwarded_to_return_command(self):
        """Machine IDs from Paginated.items must be forwarded to CreateReturnRequestCommand."""
        from orb.application.dto.commands import CreateReturnRequestCommand

        paginated = _make_paginated("k-aaa", "k-bbb", "k-ccc")

        mock_query_bus = MagicMock()
        mock_query_bus.execute = AsyncMock(return_value=paginated)

        received_cmds: list = []

        async def _capture(cmd):
            received_cmds.append(cmd)
            if isinstance(cmd, CreateReturnRequestCommand):
                cmd.created_request_ids = ["ret-req-999"]

        mock_command_bus = MagicMock()
        mock_command_bus.execute = AsyncMock(side_effect=_capture)

        orchestrator = ReturnMachinesOrchestrator(
            command_bus=mock_command_bus,
            query_bus=mock_query_bus,
            logger=_make_logger(),
        )

        await orchestrator.execute(ReturnMachinesInput(all_machines=True, force=True))

        return_cmd = next(c for c in received_cmds if isinstance(c, CreateReturnRequestCommand))
        assert sorted(return_cmd.machine_ids) == ["k-aaa", "k-bbb", "k-ccc"]


# ---------------------------------------------------------------------------
# StopMachinesOrchestrator
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.application
class TestStopMachinesPaginatedRegression:
    """Verify StopMachinesOrchestrator handles Paginated result from ListMachinesQuery."""

    @pytest.mark.asyncio
    async def test_all_machines_with_paginated_result_does_not_raise(self):
        """The --all path must not raise TypeError when query bus returns Paginated."""
        from orb.application.provider.commands import ExecuteProviderOperationCommand

        paginated = _make_paginated("m-001", "m-002")

        mock_query_bus = MagicMock()
        mock_query_bus.execute = AsyncMock(return_value=paginated)

        async def _provider_success(cmd):
            if isinstance(cmd, ExecuteProviderOperationCommand):
                cmd.result = {
                    "success": True,
                    "data": {"results": {"m-001": True, "m-002": True}},
                }

        mock_command_bus = MagicMock()
        mock_command_bus.execute = AsyncMock(side_effect=_provider_success)

        orchestrator = StopMachinesOrchestrator(
            command_bus=mock_command_bus,
            query_bus=mock_query_bus,
            logger=_make_logger(),
            provider_registry_service=_make_provider_registry_service(),
        )

        result = await orchestrator.execute(StopMachinesInput(all_machines=True, force=True))

        assert result.success is True
        assert sorted(result.stopped_machines) == ["m-001", "m-002"]
        assert result.failed_machines == []

    @pytest.mark.asyncio
    async def test_all_machines_paginated_empty_items_stops_nothing(self):
        """Empty Paginated.items must produce 'No machines to stop' without calling provider."""
        paginated = _make_paginated()

        mock_query_bus = MagicMock()
        mock_query_bus.execute = AsyncMock(return_value=paginated)
        mock_command_bus = MagicMock()
        mock_command_bus.execute = AsyncMock()

        orchestrator = StopMachinesOrchestrator(
            command_bus=mock_command_bus,
            query_bus=mock_query_bus,
            logger=_make_logger(),
            provider_registry_service=_make_provider_registry_service(),
        )

        result = await orchestrator.execute(StopMachinesInput(all_machines=True))

        mock_command_bus.execute.assert_not_called()
        assert result.success is True
        assert result.message == "No machines to stop"


# ---------------------------------------------------------------------------
# StartMachinesOrchestrator
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.application
class TestStartMachinesPaginatedRegression:
    """Verify StartMachinesOrchestrator handles Paginated result from ListMachinesQuery."""

    @pytest.mark.asyncio
    async def test_all_machines_with_paginated_result_does_not_raise(self):
        """The --all path must not raise TypeError when query bus returns Paginated."""
        from orb.application.provider.commands import ExecuteProviderOperationCommand

        paginated = _make_paginated("m-001", "m-002")

        mock_query_bus = MagicMock()
        mock_query_bus.execute = AsyncMock(return_value=paginated)

        async def _provider_success(cmd):
            if isinstance(cmd, ExecuteProviderOperationCommand):
                cmd.result = {
                    "success": True,
                    "data": {"results": {"m-001": True, "m-002": True}},
                }

        mock_command_bus = MagicMock()
        mock_command_bus.execute = AsyncMock(side_effect=_provider_success)

        orchestrator = StartMachinesOrchestrator(
            command_bus=mock_command_bus,
            query_bus=mock_query_bus,
            logger=_make_logger(),
            provider_registry_service=_make_provider_registry_service(),
        )

        result = await orchestrator.execute(StartMachinesInput(all_machines=True))

        assert result.success is True
        assert sorted(result.started_machines) == ["m-001", "m-002"]
        assert result.failed_machines == []

    @pytest.mark.asyncio
    async def test_all_machines_paginated_empty_items_starts_nothing(self):
        """Empty Paginated.items must produce 'No machines to start' without calling provider."""
        paginated = _make_paginated()

        mock_query_bus = MagicMock()
        mock_query_bus.execute = AsyncMock(return_value=paginated)
        mock_command_bus = MagicMock()
        mock_command_bus.execute = AsyncMock()

        orchestrator = StartMachinesOrchestrator(
            command_bus=mock_command_bus,
            query_bus=mock_query_bus,
            logger=_make_logger(),
            provider_registry_service=_make_provider_registry_service(),
        )

        result = await orchestrator.execute(StartMachinesInput(all_machines=True))

        mock_command_bus.execute.assert_not_called()
        assert result.success is True
        assert result.message == "No machines to start"
