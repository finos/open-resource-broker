"""Regression tests: return-request handler must NOT write COMPLETED on termination accept.

Bug: _execute_deprovisioning_for_request wrote COMPLETED the moment AWS
TerminateInstances returned success.  At that point instances are only
shutting-down, not yet terminated.

Fix: write IN_PROGRESS ("termination accepted: waiting …") so that the
background poller can transition to COMPLETED only when all instances reach
the terminated state.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.commands.request_creation_handlers import CreateReturnRequestHandler
from orb.domain.request.request_types import RequestStatus

# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------


def _make_uow_factory(machines: list[MagicMock]) -> MagicMock:
    uow = MagicMock()
    uow.machines.get_by_id.side_effect = lambda mid: next(
        (m for m in machines if m.machine_id.value == mid), None
    )
    uow.machines.save = MagicMock()
    uow.requests.get_by_id.return_value = None

    @contextmanager
    def _create():
        yield uow

    factory = MagicMock()
    factory.create_unit_of_work.side_effect = _create
    return factory


def _make_request(request_id: str = "req-001") -> MagicMock:
    req = MagicMock()
    req.request_id = request_id
    req.template_id = "tpl-1"
    req.provider_name = "aws"
    req.provider_type = "aws"
    return req


def _make_machine(machine_id: str) -> MagicMock:
    m = MagicMock()
    m.machine_id = MagicMock()
    m.machine_id.value = machine_id
    m.template_id = "tpl-1"
    m.request_id = "req-origin-001"
    m.update_status.return_value = m
    m.model_copy.return_value = m
    return m


def _make_handler() -> tuple[CreateReturnRequestHandler, MagicMock]:
    """Return (handler, command_bus_mock)."""
    logger = MagicMock()
    container = MagicMock()
    event_publisher = MagicMock()
    error_handler = MagicMock()
    query_bus = MagicMock()
    query_bus.execute = AsyncMock()
    provider_selection_port = MagicMock()
    provider_selection_port.execute_operation = AsyncMock()

    uow_factory = _make_uow_factory([_make_machine("i-aaa"), _make_machine("i-bbb")])

    # Command bus is obtained via container.get(CommandBusPort)
    command_bus = MagicMock()
    command_bus.execute = AsyncMock()
    container.get = MagicMock(return_value=command_bus)

    handler = CreateReturnRequestHandler(
        uow_factory=uow_factory,
        logger=logger,
        container=container,
        event_publisher=event_publisher,
        error_handler=error_handler,
        query_bus=query_bus,
        provider_selection_port=provider_selection_port,
    )
    return handler, command_bus


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.application
class TestReturnRequestHandlerStatus:
    """Verify that termination-accepted path writes IN_PROGRESS, not COMPLETED."""

    @pytest.mark.asyncio
    async def test_termination_accepted_writes_in_progress_not_completed(self):
        """Core regression: handler must write IN_PROGRESS when deprovisioning succeeds."""
        handler, command_bus = _make_handler()
        request = _make_request()

        # Patch deprovisioning orchestrator to simulate successful termination accept
        handler._deprovisioning_orchestrator.execute_deprovisioning = AsyncMock(
            return_value={"success": True, "successful_operations": 1, "failed_operations": 0}
        )
        # Patch machine grouping to return one group, no skipped IDs
        handler._machine_grouping_service.group_by_resource = MagicMock(
            return_value=({("aws", "RunInstances", "fleet-1"): [MagicMock()]}, [])
        )

        await handler._execute_deprovisioning_for_request(["i-aaa", "i-bbb"], request, "aws")

        # Gather all UpdateRequestStatusCommand calls
        from orb.application.dto.commands import UpdateRequestStatusCommand

        status_calls = [
            c
            for c in command_bus.execute.call_args_list
            if isinstance(c.args[0], UpdateRequestStatusCommand)
        ]

        statuses_written = [c.args[0].status for c in status_calls]

        # Must NOT contain COMPLETED
        assert RequestStatus.COMPLETED not in statuses_written, (
            "Handler wrote COMPLETED immediately on termination accept — bug is not fixed"
        )

        # Must contain IN_PROGRESS (either from the initial transition or from the
        # termination-accepted path)
        assert RequestStatus.IN_PROGRESS in statuses_written, (
            "Handler did not write IN_PROGRESS after termination was accepted"
        )

    @pytest.mark.asyncio
    async def test_termination_accepted_message_is_honest(self):
        """Status message must not say 'termination initiated' as if it's done."""
        handler, command_bus = _make_handler()
        request = _make_request()

        handler._deprovisioning_orchestrator.execute_deprovisioning = AsyncMock(
            return_value={"success": True, "successful_operations": 1, "failed_operations": 0}
        )
        handler._machine_grouping_service.group_by_resource = MagicMock(
            return_value=({("aws", "RunInstances", "fleet-1"): [MagicMock()]}, [])
        )

        await handler._execute_deprovisioning_for_request(["i-aaa"], request, "aws")

        from orb.application.dto.commands import UpdateRequestStatusCommand

        status_calls = [
            c
            for c in command_bus.execute.call_args_list
            if isinstance(c.args[0], UpdateRequestStatusCommand)
            and c.args[0].status == RequestStatus.IN_PROGRESS
        ]
        messages = [c.args[0].message for c in status_calls]

        # At least one IN_PROGRESS message must be about "waiting" or "accepted"
        honest_messages = [
            m
            for m in messages
            if "waiting" in m.lower() or "accepted" in m.lower() or "terminating" in m.lower()
        ]
        assert honest_messages, (
            f"No honest termination-in-progress message found. Messages: {messages}"
        )

    @pytest.mark.asyncio
    async def test_deprovisioning_failure_writes_failed_not_completed(self):
        """On deprovisioning failure, write FAILED, never COMPLETED."""
        handler, command_bus = _make_handler()
        request = _make_request()

        handler._deprovisioning_orchestrator.execute_deprovisioning = AsyncMock(
            return_value={
                "success": False,
                "errors": ["TerminateInstances throttled"],
                "failed_operations": 1,
            }
        )
        handler._machine_grouping_service.group_by_resource = MagicMock(
            return_value=({("aws", "RunInstances", "fleet-1"): [MagicMock()]}, [])
        )

        await handler._execute_deprovisioning_for_request(["i-aaa"], request, "aws")

        from orb.application.dto.commands import UpdateRequestStatusCommand

        status_calls = [
            c
            for c in command_bus.execute.call_args_list
            if isinstance(c.args[0], UpdateRequestStatusCommand)
        ]
        statuses_written = [c.args[0].status for c in status_calls]

        assert RequestStatus.COMPLETED not in statuses_written
        assert RequestStatus.FAILED in statuses_written
