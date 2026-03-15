"""Unit tests for --wait polling loop in handle_request_machines."""

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.console_port import ConsolePort
from orb.domain.base.ports.scheduler_port import SchedulerPort
from orb.infrastructure.di.buses import CommandBus, QueryBus


def _make_namespace(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _mock_container(console=None):
    """Return (container, command_bus, query_bus, scheduler, console) mocks."""
    container = MagicMock()
    command_bus = AsyncMock()
    query_bus = AsyncMock()
    scheduler = MagicMock()
    logging_port = MagicMock()
    console_port = console or MagicMock()

    dispatch_map = {
        CommandBus: command_bus,
        QueryBus: query_bus,
        SchedulerPort: scheduler,
        LoggingPort: logging_port,
        ConsolePort: console_port,
    }
    container.get.side_effect = lambda t: dispatch_map.get(t, MagicMock())
    return container, command_bus, query_bus, scheduler, console_port


def _base_patches(container):
    return (
        patch("orb.interface.request_command_handlers.get_container", return_value=container),
        patch(
            "orb.domain.request.request_identifiers.RequestId.generate",
            return_value=MagicMock(__str__=lambda self: "req-fixed"),
        ),
        patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active",
            return_value=False,
        ),
    )


def _setup_scheduler(scheduler, template_id="t1", count=1):
    scheduler.parse_request_data.return_value = {
        "template_id": template_id,
        "requested_count": count,
    }
    scheduler.format_request_response.return_value = {"requestId": "req-fixed"}
    scheduler.get_exit_code_for_status.return_value = 0


def _make_dto(status: str) -> MagicMock:
    dto = MagicMock()
    dto.status = status
    dto.resource_ids = []
    dto.metadata = {}
    return dto


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestMachinesWait:
    @pytest.mark.asyncio
    async def test_no_wait_skips_polling(self):
        """wait=False → query_bus called exactly once (initial fetch), no sleep."""
        container, command_bus, query_bus, scheduler, console = _mock_container()
        _setup_scheduler(scheduler)
        query_bus.execute.return_value = _make_dto("pending")

        args = _make_namespace(template_id="t1", machine_count=1, metadata={}, wait=False)

        p1, p2, p3 = _base_patches(container)
        with p1, p2, p3, patch("asyncio.sleep") as mock_sleep:
            from orb.interface.request_command_handlers import handle_request_machines

            await handle_request_machines(args)

        mock_sleep.assert_not_awaited()
        # Only the initial GetRequestQuery, no polling queries
        assert query_bus.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_timeout_zero_skips_polling(self):
        """wait=True but timeout=0 → no polling, no sleep."""
        container, command_bus, query_bus, scheduler, console = _mock_container()
        _setup_scheduler(scheduler)
        query_bus.execute.return_value = _make_dto("pending")

        args = _make_namespace(
            template_id="t1", machine_count=1, metadata={}, wait=True, timeout=0
        )

        p1, p2, p3 = _base_patches(container)
        with p1, p2, p3, patch("asyncio.sleep") as mock_sleep:
            from orb.interface.request_command_handlers import handle_request_machines

            await handle_request_machines(args)

        mock_sleep.assert_not_awaited()
        assert query_bus.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_already_terminal_returns_immediately(self):
        """wait=True, first poll returns 'complete' → no sleep, returns immediately."""
        container, command_bus, query_bus, scheduler, console = _mock_container()
        _setup_scheduler(scheduler)
        # Initial fetch (after command) + first poll both return complete
        query_bus.execute.return_value = _make_dto("complete")

        args = _make_namespace(
            template_id="t1", machine_count=1, metadata={}, wait=True, timeout=300
        )

        p1, p2, p3 = _base_patches(container)
        with p1, p2, p3, patch("asyncio.sleep") as mock_sleep:
            from orb.interface.request_command_handlers import handle_request_machines

            result = await handle_request_machines(args)

        mock_sleep.assert_not_awaited()
        assert isinstance(result, tuple)
        response, exit_code = result
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_polls_until_terminal(self):
        """wait=True, two 'pending' responses then 'complete' → sleep called twice."""
        container, command_bus, query_bus, scheduler, console = _mock_container()
        _setup_scheduler(scheduler)

        # Sequence: initial fetch=pending, poll1=pending, poll2=pending, poll3=complete
        query_bus.execute.side_effect = [
            _make_dto("pending"),   # initial fetch after command
            _make_dto("pending"),   # poll 1
            _make_dto("pending"),   # poll 2
            _make_dto("complete"),  # poll 3 — terminal
        ]

        args = _make_namespace(
            template_id="t1", machine_count=1, metadata={}, wait=True, timeout=300
        )

        p1, p2, p3 = _base_patches(container)
        with p1, p2, p3, patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            from orb.interface.request_command_handlers import handle_request_machines

            result = await handle_request_machines(args)

        # sleep called for poll1 and poll2 (not after terminal)
        assert mock_sleep.await_count == 2
        assert isinstance(result, tuple)
        _, exit_code = result
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_timeout_raises_systemexit(self):
        """wait=True, always pending, short timeout → SystemExit(1) raised."""
        container, command_bus, query_bus, scheduler, console = _mock_container()
        _setup_scheduler(scheduler)

        # Always return pending
        query_bus.execute.return_value = _make_dto("pending")

        args = _make_namespace(
            template_id="t1", machine_count=1, metadata={}, wait=True, timeout=1
        )

        p1, p2, p3 = _base_patches(container)
        # Freeze time so deadline is immediately exceeded after first poll
        with p1, p2, p3, patch("asyncio.sleep", new_callable=AsyncMock), patch(
            "time.monotonic", side_effect=[0.0, 0.0, 2.0]
        ):
            from orb.interface.request_command_handlers import handle_request_machines

            with pytest.raises(SystemExit) as exc_info:
                await handle_request_machines(args)

        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_failed_status_is_terminal(self):
        """wait=True, first poll returns 'failed' → polling stops, no sleep."""
        container, command_bus, query_bus, scheduler, console = _mock_container()
        _setup_scheduler(scheduler)
        scheduler.get_exit_code_for_status.return_value = 1

        query_bus.execute.return_value = _make_dto("failed")

        args = _make_namespace(
            template_id="t1", machine_count=1, metadata={}, wait=True, timeout=300
        )

        p1, p2, p3 = _base_patches(container)
        with p1, p2, p3, patch("asyncio.sleep") as mock_sleep:
            from orb.interface.request_command_handlers import handle_request_machines

            result = await handle_request_machines(args)

        mock_sleep.assert_not_awaited()
        assert isinstance(result, tuple)
        _, exit_code = result
        assert exit_code == 1
