"""Unit tests for request command handlers in the interface layer."""

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.dto.commands import (
    CancelRequestCommand,
    CreateRequestCommand,
    CreateReturnRequestCommand,
)
from orb.application.dto.queries import (
    GetRequestQuery,
    ListActiveRequestsQuery,
    ListMachinesQuery,
    ListReturnRequestsQuery,
)
from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.scheduler_port import SchedulerPort
from orb.infrastructure.di.buses import CommandBus, QueryBus
from orb.interface.request_command_handlers import (
    handle_cancel_request,
    handle_get_request_status,
    handle_get_return_requests,
    handle_request_machines,
    handle_request_return_machines,
)


def _make_namespace(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _mock_container():
    """Return (container, command_bus, query_bus, scheduler) mocks."""
    container = MagicMock()
    command_bus = AsyncMock()
    query_bus = AsyncMock()
    scheduler = MagicMock()
    logging_port = MagicMock()

    dispatch_map = {
        CommandBus: command_bus,
        QueryBus: query_bus,
        SchedulerPort: scheduler,
        LoggingPort: logging_port,
    }
    container.get.side_effect = lambda t: dispatch_map.get(t, MagicMock())
    return container, command_bus, query_bus, scheduler


# ---------------------------------------------------------------------------
# handle_request_machines
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleRequestMachines:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        """template_id + machine_count → CreateRequestCommand dispatched, tuple returned."""
        container, command_bus, query_bus, scheduler = _mock_container()

        scheduler.parse_request_data.return_value = {
            "template_id": "t1",
            "requested_count": 3,
        }
        scheduler.format_request_response.return_value = {"requestId": "req-abc"}
        scheduler.get_exit_code_for_status.return_value = 0

        request_dto = MagicMock()
        request_dto.status = "pending"
        request_dto.resource_ids = ["r-1"]
        request_dto.metadata = {}
        query_bus.execute.return_value = request_dto

        args = _make_namespace(template_id="t1", machine_count=3, metadata={})

        with (
            patch("orb.interface.request_command_handlers.get_container", return_value=container),
            patch(
                "orb.api.utils.request_id_generator.generate_request_id",
                return_value="req-fixed",
            ),
            patch(
                "orb.infrastructure.mocking.dry_run_context.is_dry_run_active",
                return_value=False,
            ),
        ):
            result = await handle_request_machines(args)

        assert isinstance(result, tuple)
        response, exit_code = result
        assert exit_code == 0
        assert response == {"requestId": "req-abc"}

        command_bus.execute.assert_awaited_once()
        cmd = command_bus.execute.call_args[0][0]
        assert isinstance(cmd, CreateRequestCommand)
        assert cmd.template_id == "t1"
        assert cmd.requested_count == 3
        assert cmd.request_id == "req-fixed"

    @pytest.mark.asyncio
    async def test_from_input_data(self):
        """input_data dict is passed to scheduler.parse_request_data."""
        container, command_bus, query_bus, scheduler = _mock_container()

        scheduler.parse_request_data.return_value = {
            "template_id": "t1",
            "requested_count": 2,
        }
        scheduler.format_request_response.return_value = {"requestId": "req-xyz"}
        scheduler.get_exit_code_for_status.return_value = 0

        request_dto = MagicMock()
        request_dto.status = "pending"
        request_dto.resource_ids = []
        request_dto.metadata = {}
        query_bus.execute.return_value = request_dto

        args = _make_namespace(
            input_data={"template_id": "t1", "requested_count": 2},
            metadata={},
        )

        with (
            patch("orb.interface.request_command_handlers.get_container", return_value=container),
            patch(
                "orb.api.utils.request_id_generator.generate_request_id",
                return_value="req-fixed",
            ),
            patch(
                "orb.infrastructure.mocking.dry_run_context.is_dry_run_active",
                return_value=False,
            ),
        ):
            result = await handle_request_machines(args)

        scheduler.parse_request_data.assert_called_once_with(
            {"template_id": "t1", "requested_count": 2}
        )
        assert isinstance(result, tuple)

    @pytest.mark.asyncio
    async def test_missing_template_id(self):
        """No template_id → error dict returned (not a tuple)."""
        container, command_bus, query_bus, scheduler = _mock_container()
        scheduler.parse_request_data.return_value = {"requested_count": 3}

        args = _make_namespace(machine_count=3, metadata={})

        with (
            patch("orb.interface.request_command_handlers.get_container", return_value=container),
            patch(
                "orb.infrastructure.mocking.dry_run_context.is_dry_run_active",
                return_value=False,
            ),
        ):
            result = await handle_request_machines(args)

        assert isinstance(result, dict)
        assert "Template ID is required" in result["error"]
        command_bus.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_count(self):
        """requested_count=0 (falsy) → error dict returned."""
        container, command_bus, query_bus, scheduler = _mock_container()
        scheduler.parse_request_data.return_value = {
            "template_id": "t1",
            "requested_count": 0,
        }

        args = _make_namespace(template_id="t1", metadata={})

        with (
            patch("orb.interface.request_command_handlers.get_container", return_value=container),
            patch(
                "orb.infrastructure.mocking.dry_run_context.is_dry_run_active",
                return_value=False,
            ),
        ):
            result = await handle_request_machines(args)

        assert isinstance(result, dict)
        assert "Machine count is required" in result["error"]
        command_bus.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_command_failure(self):
        """command_bus.execute raises → error tuple with exit_code=1."""
        container, command_bus, query_bus, scheduler = _mock_container()

        scheduler.parse_request_data.return_value = {
            "template_id": "t1",
            "requested_count": 2,
        }
        scheduler.format_request_response.return_value = {"status": "failed"}
        command_bus.execute.side_effect = Exception("boom")

        args = _make_namespace(template_id="t1", machine_count=2, metadata={})

        with (
            patch("orb.interface.request_command_handlers.get_container", return_value=container),
            patch(
                "orb.api.utils.request_id_generator.generate_request_id",
                return_value="req-fixed",
            ),
            patch(
                "orb.infrastructure.mocking.dry_run_context.is_dry_run_active",
                return_value=False,
            ),
        ):
            result = await handle_request_machines(args)

        assert isinstance(result, tuple)
        response, exit_code = result
        assert exit_code == 1
        assert response["status"] == "failed"


# ---------------------------------------------------------------------------
# handle_request_return_machines
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleRequestReturnMachines:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        """machine_ids provided → CreateReturnRequestCommand dispatched."""
        container, command_bus, query_bus, scheduler = _mock_container()
        scheduler.format_request_response.return_value = {"requestId": None, "status": "pending"}

        args = _make_namespace(machine_ids=["i-1", "i-2"])

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_request_return_machines(args)

        command_bus.execute.assert_awaited_once()
        cmd = command_bus.execute.call_args[0][0]
        assert isinstance(cmd, CreateReturnRequestCommand)
        assert cmd.machine_ids == ["i-1", "i-2"]
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_from_input_data(self):
        """input_data with machineId keys → IDs extracted correctly."""
        container, command_bus, query_bus, scheduler = _mock_container()
        scheduler.format_request_response.return_value = {"status": "pending"}

        args = _make_namespace(
            input_data={"machines": [{"machineId": "i-1"}, {"machineId": "i-2"}]}
        )

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            _result = await handle_request_return_machines(args)

        cmd = command_bus.execute.call_args[0][0]
        assert cmd.machine_ids == ["i-1", "i-2"]

    @pytest.mark.asyncio
    async def test_empty_ids_returns_error(self):
        """No machine_ids, no input_data, all=False → error dict."""
        container, command_bus, query_bus, scheduler = _mock_container()

        args = _make_namespace(machine_ids=[], all=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_request_return_machines(args)

        assert isinstance(result, dict)
        assert "Machine IDs are required" in result["error"]
        command_bus.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_without_force_returns_error(self):
        """all=True, force=False → destructive operation error."""
        container, command_bus, query_bus, scheduler = _mock_container()

        args = _make_namespace(all=True, force=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_request_return_machines(args)

        assert isinstance(result, dict)
        assert "--force" in result["error"]
        command_bus.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_with_force(self):
        """all=True, force=True → ListMachinesQuery dispatched, then CreateReturnRequestCommand."""
        container, command_bus, query_bus, scheduler = _mock_container()
        scheduler.format_request_response.return_value = {"status": "pending"}

        machine_dto_1 = MagicMock()
        machine_dto_1.machine_id = "i-1"
        machine_dto_2 = MagicMock()
        machine_dto_2.machine_id = "i-2"
        query_bus.execute.return_value = [machine_dto_1, machine_dto_2]

        args = _make_namespace(all=True, force=True)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            _result = await handle_request_return_machines(args)

        query_bus.execute.assert_awaited_once()
        list_query = query_bus.execute.call_args[0][0]
        assert isinstance(list_query, ListMachinesQuery)
        assert list_query.all_resources is True
        assert list_query.active_only is True

        cmd = command_bus.execute.call_args[0][0]
        assert isinstance(cmd, CreateReturnRequestCommand)
        assert cmd.machine_ids == ["i-1", "i-2"]

    @pytest.mark.asyncio
    async def test_all_no_active_machines(self):
        """all=True, force=True, query returns [] → error dict."""
        container, command_bus, query_bus, scheduler = _mock_container()
        query_bus.execute.return_value = []

        args = _make_namespace(all=True, force=True)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_request_return_machines(args)

        assert isinstance(result, dict)
        assert "No active machines found" in result["error"]
        command_bus.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_cancel_request
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleCancelRequest:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        """request_id provided → CancelRequestCommand dispatched, status=cancelled."""
        container, command_bus, query_bus, scheduler = _mock_container()
        scheduler.format_request_response.return_value = {
            "request_id": "req-123",
            "status": "cancelled",
        }

        args = _make_namespace(request_id="req-123")

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_cancel_request(args)

        command_bus.execute.assert_awaited_once()
        cmd = command_bus.execute.call_args[0][0]
        assert isinstance(cmd, CancelRequestCommand)
        assert cmd.request_id == "req-123"
        assert isinstance(result, dict)
        assert result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_with_reason(self):
        """reason attribute is passed through to CancelRequestCommand."""
        container, command_bus, query_bus, scheduler = _mock_container()
        scheduler.format_request_response.return_value = {"status": "cancelled"}

        args = _make_namespace(request_id="req-123", reason="done")

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_cancel_request(args)

        cmd = command_bus.execute.call_args[0][0]
        assert cmd.reason == "done"

    @pytest.mark.asyncio
    async def test_missing_request_id(self):
        """No request_id → error dict, command not dispatched."""
        container, command_bus, query_bus, scheduler = _mock_container()

        args = _make_namespace()

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_cancel_request(args)

        assert isinstance(result, dict)
        assert "Request ID is required" in result["error"]
        command_bus.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_get_return_requests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleGetReturnRequests:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        """No input_data → ListReturnRequestsQuery with empty machine_names."""
        container, command_bus, query_bus, scheduler = _mock_container()
        query_bus.execute.return_value = []
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace()

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_get_return_requests(args)

        query_bus.execute.assert_awaited_once()
        q = query_bus.execute.call_args[0][0]
        assert isinstance(q, ListReturnRequestsQuery)
        assert q.machine_names == []
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_with_machine_name_filter(self):
        """input_data with machines[].name → query has machine_names populated."""
        container, command_bus, query_bus, scheduler = _mock_container()
        query_bus.execute.return_value = []
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace(input_data={"machines": [{"name": "m1"}, {"name": "m2"}]})

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_get_return_requests(args)

        q = query_bus.execute.call_args[0][0]
        assert q.machine_names == ["m1", "m2"]


# ---------------------------------------------------------------------------
# handle_get_request_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleGetRequestStatus:
    @pytest.mark.asyncio
    async def test_single_id(self):
        """request_id provided → GetRequestQuery dispatched."""
        container, command_bus, query_bus, scheduler = _mock_container()

        request_dto = MagicMock()
        query_bus.execute.return_value = request_dto
        scheduler.parse_request_data.return_value = [{"request_id": "req-123"}]
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace(request_id="req-123", all=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            _result = await handle_get_request_status(args)

        query_bus.execute.assert_awaited_once()
        q = query_bus.execute.call_args[0][0]
        assert isinstance(q, GetRequestQuery)
        assert q.request_id == "req-123"

    @pytest.mark.asyncio
    async def test_all_flag(self):
        """all=True → ListActiveRequestsQuery dispatched."""
        container, command_bus, query_bus, scheduler = _mock_container()
        query_bus.execute.return_value = []
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace(all=True)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            _result = await handle_get_request_status(args)

        query_bus.execute.assert_awaited_once()
        q = query_bus.execute.call_args[0][0]
        assert isinstance(q, ListActiveRequestsQuery)
        assert q.all_resources is True

    @pytest.mark.asyncio
    async def test_all_with_specific_ids_returns_error(self):
        """all=True + request_ids → error dict, no query dispatched."""
        container, command_bus, query_bus, scheduler = _mock_container()

        args = _make_namespace(all=True, request_ids=["req-1"])

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_get_request_status(args)

        assert isinstance(result, dict)
        assert "Cannot use --all with specific request IDs" in result["error"]
        query_bus.execute.assert_not_awaited()
