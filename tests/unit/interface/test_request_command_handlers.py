"""Unit tests for request command handlers in the interface layer."""

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.ports.scheduler_port import SchedulerPort
from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
from orb.application.services.orchestration.dtos import (
    AcquireMachinesOutput,
    CancelRequestOutput,
    GetRequestStatusOutput,
    ListRequestsOutput,
    ListReturnRequestsOutput,
    ReturnMachinesOutput,
)
from orb.application.services.orchestration.get_request_status import GetRequestStatusOrchestrator
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
from orb.application.services.orchestration.list_return_requests import (
    ListReturnRequestsOrchestrator,
)
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
from orb.interface.request_command_handlers import (
    handle_cancel_request,
    handle_get_request_status,
    handle_get_return_requests,
    handle_list_requests,
    handle_request_machines,
    handle_request_return_machines,
)


def _make_namespace(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _mock_container(extra: dict | None = None):
    """Return (container, scheduler) mocks with orchestrators pre-wired."""
    container = MagicMock()
    scheduler = MagicMock(spec=SchedulerPort)

    acquire_orch = AsyncMock(spec=AcquireMachinesOrchestrator)
    cancel_orch = AsyncMock(spec=CancelRequestOrchestrator)
    status_orch = AsyncMock(spec=GetRequestStatusOrchestrator)
    list_req_orch = AsyncMock(spec=ListRequestsOrchestrator)
    list_ret_orch = AsyncMock(spec=ListReturnRequestsOrchestrator)
    return_orch = AsyncMock(spec=ReturnMachinesOrchestrator)

    dispatch_map: dict = {
        SchedulerPort: scheduler,
        AcquireMachinesOrchestrator: acquire_orch,
        CancelRequestOrchestrator: cancel_orch,
        GetRequestStatusOrchestrator: status_orch,
        ListRequestsOrchestrator: list_req_orch,
        ListReturnRequestsOrchestrator: list_ret_orch,
        ReturnMachinesOrchestrator: return_orch,
    }
    if extra:
        dispatch_map.update(extra)

    container.get.side_effect = lambda t: dispatch_map.get(t, MagicMock())
    return (
        container,
        scheduler,
        acquire_orch,
        cancel_orch,
        status_orch,
        list_req_orch,
        list_ret_orch,
        return_orch,
    )


# ---------------------------------------------------------------------------
# handle_request_machines
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleRequestMachines:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        """template_id + machine_count → AcquireMachinesOrchestrator called, tuple returned."""
        container, scheduler, acquire_orch, *_ = _mock_container()

        scheduler.parse_request_data.return_value = {
            "template_id": "t1",
            "requested_count": 3,
        }
        acquire_orch.execute.return_value = AcquireMachinesOutput(
            request_id="req-abc", status="pending", machine_ids=["r-1"]
        )
        scheduler.format_request_response.return_value = {"requestId": "req-abc"}
        scheduler.get_exit_code_for_status.return_value = 0

        args = _make_namespace(template_id="t1", machine_count=3, metadata={})

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_request_machines(args)

        assert isinstance(result, tuple)
        response, exit_code = result
        assert exit_code == 0
        assert response == {"requestId": "req-abc"}
        acquire_orch.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_from_input_data(self):
        """input_data dict is passed to scheduler.parse_request_data."""
        container, scheduler, acquire_orch, *_ = _mock_container()

        scheduler.parse_request_data.return_value = {
            "template_id": "t1",
            "requested_count": 2,
        }
        acquire_orch.execute.return_value = AcquireMachinesOutput(
            request_id="req-xyz", status="pending"
        )
        scheduler.format_request_response.return_value = {"requestId": "req-xyz"}
        scheduler.get_exit_code_for_status.return_value = 0

        args = _make_namespace(
            input_data={"template_id": "t1", "requested_count": 2},
            metadata={},
        )

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_request_machines(args)

        scheduler.parse_request_data.assert_called_once_with(
            {"template_id": "t1", "requested_count": 2}
        )
        assert isinstance(result, tuple)

    @pytest.mark.asyncio
    async def test_missing_template_id(self):
        """No template_id → error dict returned (not a tuple)."""
        container, scheduler, *_ = _mock_container()
        scheduler.parse_request_data.return_value = {"requested_count": 3}

        args = _make_namespace(machine_count=3, metadata={})

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_request_machines(args)

        assert isinstance(result, dict)
        assert "Template ID is required" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_count(self):
        """requested_count=0 (falsy) → error dict returned."""
        container, scheduler, *_ = _mock_container()
        scheduler.parse_request_data.return_value = {
            "template_id": "t1",
            "requested_count": 0,
        }

        args = _make_namespace(template_id="t1", metadata={})

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_request_machines(args)

        assert isinstance(result, dict)
        assert "Machine count is required" in result["error"]


# ---------------------------------------------------------------------------
# handle_request_return_machines
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleRequestReturnMachines:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        """machine_ids provided → ReturnMachinesOrchestrator called."""
        container, scheduler, _, _, _, _, _, return_orch = _mock_container()
        return_orch.execute.return_value = ReturnMachinesOutput(
            request_id="ret-1", status="pending"
        )
        scheduler.format_request_response.return_value = {"requestId": "ret-1", "status": "pending"}

        args = _make_namespace(machine_ids=["i-1", "i-2"])

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_request_return_machines(args)

        return_orch.execute.assert_awaited_once()
        call_input = return_orch.execute.call_args[0][0]
        assert call_input.machine_ids == ["i-1", "i-2"]
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_from_input_data(self):
        """input_data with machineId keys → IDs extracted correctly."""
        container, scheduler, _, _, _, _, _, return_orch = _mock_container()
        return_orch.execute.return_value = ReturnMachinesOutput(
            request_id="ret-1", status="pending"
        )
        scheduler.format_request_response.return_value = {"status": "pending"}

        args = _make_namespace(
            input_data={"machines": [{"machineId": "i-1"}, {"machineId": "i-2"}]}
        )

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_request_return_machines(args)

        call_input = return_orch.execute.call_args[0][0]
        assert call_input.machine_ids == ["i-1", "i-2"]

    @pytest.mark.asyncio
    async def test_empty_ids_returns_error(self):
        """No machine_ids, no input_data, all=False → error dict."""
        container, *_ = _mock_container()

        args = _make_namespace(machine_ids=[], all=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_request_return_machines(args)

        assert isinstance(result, dict)
        assert "Machine IDs are required" in result["error"]

    @pytest.mark.asyncio
    async def test_all_without_force_returns_error(self):
        """all=True, force=False → destructive operation error."""
        container, *_ = _mock_container()

        args = _make_namespace(all=True, force=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_request_return_machines(args)

        assert isinstance(result, dict)
        assert "--force" in result["error"]

    @pytest.mark.asyncio
    async def test_all_with_force(self):
        """all=True, force=True → ReturnMachinesOrchestrator called with all_machines=True."""
        container, scheduler, _, _, _, _, _, return_orch = _mock_container()
        return_orch.execute.return_value = ReturnMachinesOutput(
            request_id="ret-1", status="pending"
        )
        scheduler.format_request_response.return_value = {"status": "pending"}

        args = _make_namespace(all=True, force=True)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_request_return_machines(args)

        call_input = return_orch.execute.call_args[0][0]
        assert call_input.all_machines is True
        assert call_input.force is True


# ---------------------------------------------------------------------------
# handle_cancel_request
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleCancelRequest:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        """request_id provided → CancelRequestOrchestrator called, status=cancelled."""
        container, scheduler, _, cancel_orch, *_ = _mock_container()
        cancel_orch.execute.return_value = CancelRequestOutput(
            request_id="req-123",
            status="cancelled",
            raw={"request_id": "req-123", "status": "cancelled"},
        )
        scheduler.format_request_response.return_value = {
            "request_id": "req-123",
            "status": "cancelled",
        }

        args = _make_namespace(request_id="req-123")

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_cancel_request(args)

        cancel_orch.execute.assert_awaited_once()
        call_input = cancel_orch.execute.call_args[0][0]
        assert call_input.request_id == "req-123"
        assert isinstance(result, dict)
        assert result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_with_reason(self):
        """reason attribute is passed through to orchestrator."""
        container, scheduler, _, cancel_orch, *_ = _mock_container()
        cancel_orch.execute.return_value = CancelRequestOutput(
            request_id="req-123",
            status="cancelled",
            raw={"request_id": "req-123", "status": "cancelled"},
        )
        scheduler.format_request_response.return_value = {"status": "cancelled"}

        args = _make_namespace(request_id="req-123", reason="done")

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_cancel_request(args)

        call_input = cancel_orch.execute.call_args[0][0]
        assert call_input.reason == "done"

    @pytest.mark.asyncio
    async def test_missing_request_id(self):
        """No request_id → error dict, orchestrator not called."""
        container, _, _, cancel_orch, *_ = _mock_container()

        args = _make_namespace()

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_cancel_request(args)

        assert isinstance(result, dict)
        assert "Request ID is required" in result["error"]
        cancel_orch.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_get_return_requests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleGetReturnRequests:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        """No input_data → ListReturnRequestsOrchestrator called."""
        container, scheduler, _, _, _, _, list_ret_orch, _ = _mock_container()
        list_ret_orch.execute.return_value = ListReturnRequestsOutput(requests=[])
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace()

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_get_return_requests(args)

        list_ret_orch.execute.assert_awaited_once()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# handle_get_request_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleGetRequestStatus:
    @pytest.mark.asyncio
    async def test_single_id(self):
        """request_id provided → GetRequestStatusOrchestrator called with that ID."""
        container, scheduler, _, _, status_orch, *_ = _mock_container()
        status_orch.execute.return_value = GetRequestStatusOutput(
            requests=[{"request_id": "req-123", "status": "complete"}]
        )
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace(request_id="req-123", all=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_get_request_status(args)

        status_orch.execute.assert_awaited_once()
        call_input = status_orch.execute.call_args[0][0]
        assert "req-123" in call_input.request_ids
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_all_flag(self):
        """all=True → GetRequestStatusOrchestrator called with all_requests=True."""
        container, scheduler, _, _, status_orch, *_ = _mock_container()
        status_orch.execute.return_value = GetRequestStatusOutput(requests=[])
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace(all=True)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_get_request_status(args)

        call_input = status_orch.execute.call_args[0][0]
        assert call_input.all_requests is True

    @pytest.mark.asyncio
    async def test_all_with_specific_ids_returns_error(self):
        """all=True + request_ids → error dict, orchestrator not called."""
        container, _, _, _, status_orch, *_ = _mock_container()

        args = _make_namespace(all=True, request_ids=["req-1"])

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_get_request_status(args)

        assert isinstance(result, dict)
        assert "Cannot use --all with specific request IDs" in result["error"]
        status_orch.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_get_request_status — detailed flag (2010) + multi-ID paths (2014)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleGetRequestStatusDetailed:
    """2010: --detailed flag must be respected, not hardcoded to True."""

    @pytest.mark.asyncio
    async def test_detailed_false_by_default(self):
        """No --detailed on args → orchestrator receives detailed=False."""
        container, scheduler, _, _, status_orch, *_ = _mock_container()
        status_orch.execute.return_value = GetRequestStatusOutput(requests=[])
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace(request_id="req-001", all=False, detailed=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_get_request_status(args)

        call_input = status_orch.execute.call_args[0][0]
        assert call_input.detailed is False
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_detailed_true_when_flag_set(self):
        """--detailed set → orchestrator receives detailed=True."""
        container, scheduler, _, _, status_orch, *_ = _mock_container()
        status_orch.execute.return_value = GetRequestStatusOutput(requests=[])
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace(request_id="req-001", all=False, detailed=True)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_get_request_status(args)

        call_input = status_orch.execute.call_args[0][0]
        assert call_input.detailed is True

    @pytest.mark.asyncio
    async def test_all_path_detailed_false_by_default(self):
        """--all path also respects detailed flag defaulting to False."""
        container, scheduler, _, _, status_orch, *_ = _mock_container()
        status_orch.execute.return_value = GetRequestStatusOutput(requests=[])
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace(all=True, detailed=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_get_request_status(args)

        call_input = status_orch.execute.call_args[0][0]
        assert call_input.detailed is False

    @pytest.mark.asyncio
    async def test_all_path_detailed_true_when_flag_set(self):
        """--all + --detailed → orchestrator receives detailed=True."""
        container, scheduler, _, _, status_orch, *_ = _mock_container()
        status_orch.execute.return_value = GetRequestStatusOutput(requests=[])
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace(all=True, detailed=True)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_get_request_status(args)

        call_input = status_orch.execute.call_args[0][0]
        assert call_input.detailed is True


@pytest.mark.unit
class TestHandleGetRequestStatusMultiId:
    """2014: multi-ID paths in handle_get_request_status."""

    @pytest.mark.asyncio
    async def test_request_ids_list_flag(self):
        """args.request_ids=['req-1','req-2'] → both IDs forwarded."""
        container, scheduler, _, _, status_orch, *_ = _mock_container()
        status_orch.execute.return_value = GetRequestStatusOutput(requests=[])
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace(request_ids=["req-1", "req-2"], all=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_get_request_status(args)

        call_input = status_orch.execute.call_args[0][0]
        assert "req-1" in call_input.request_ids
        assert "req-2" in call_input.request_ids
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_flag_request_ids_path(self):
        """args.flag_request_ids=['req-3'] → ID forwarded."""
        container, scheduler, _, _, status_orch, *_ = _mock_container()
        status_orch.execute.return_value = GetRequestStatusOutput(requests=[])
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace(flag_request_ids=["req-3"], all=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_get_request_status(args)

        call_input = status_orch.execute.call_args[0][0]
        assert "req-3" in call_input.request_ids

    @pytest.mark.asyncio
    async def test_request_id_as_list(self):
        """args.request_id is a list → all items forwarded."""
        container, scheduler, _, _, status_orch, *_ = _mock_container()
        status_orch.execute.return_value = GetRequestStatusOutput(requests=[])
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace(request_id=["req-a", "req-b"], all=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_get_request_status(args)

        call_input = status_orch.execute.call_args[0][0]
        assert "req-a" in call_input.request_ids
        assert "req-b" in call_input.request_ids

    @pytest.mark.asyncio
    async def test_combined_request_id_and_request_ids(self):
        """Scalar request_id + request_ids list → union of all IDs."""
        container, scheduler, _, _, status_orch, *_ = _mock_container()
        status_orch.execute.return_value = GetRequestStatusOutput(requests=[])
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace(request_id="req-1", request_ids=["req-2", "req-3"], all=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_get_request_status(args)

        call_input = status_orch.execute.call_args[0][0]
        assert set(call_input.request_ids) == {"req-1", "req-2", "req-3"}

    @pytest.mark.asyncio
    async def test_input_data_with_request_id_keys(self):
        """args.input_data with request_id fields → IDs extracted."""
        container, scheduler, _, _, status_orch, *_ = _mock_container()
        status_orch.execute.return_value = GetRequestStatusOutput(requests=[])
        scheduler.format_request_status_response.return_value = {"requests": []}
        scheduler.parse_request_data.return_value = [
            {"request_id": "req-x"},
            {"request_id": "req-y"},
        ]

        args = _make_namespace(
            input_data={"requests": [{"request_id": "req-x"}, {"request_id": "req-y"}]},
            all=False,
        )

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_get_request_status(args)

        call_input = status_orch.execute.call_args[0][0]
        assert "req-x" in call_input.request_ids
        assert "req-y" in call_input.request_ids

    @pytest.mark.asyncio
    async def test_no_ids_provided_returns_error(self):
        """No IDs, no input_data, all=False → error dict, orchestrator not called."""
        container, _, _, _, status_orch, *_ = _mock_container()

        args = _make_namespace(all=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_get_request_status(args)

        assert isinstance(result, dict)
        assert "error" in result
        status_orch.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_input_data_parse_returns_single_dict(self):
        """scheduler.parse_request_data returns a single dict → wrapped in list."""
        container, scheduler, _, _, status_orch, *_ = _mock_container()
        status_orch.execute.return_value = GetRequestStatusOutput(requests=[])
        scheduler.format_request_status_response.return_value = {"requests": []}
        scheduler.parse_request_data.return_value = {"request_id": "req-single"}

        args = _make_namespace(input_data={"request_id": "req-single"}, all=False)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            await handle_get_request_status(args)

        call_input = status_orch.execute.call_args[0][0]
        assert "req-single" in call_input.request_ids


# ---------------------------------------------------------------------------
# handle_list_requests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleListRequests:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        """handle_list_requests → ListRequestsOrchestrator called."""
        container, scheduler, _, _, _, list_req_orch, *_ = _mock_container()
        list_req_orch.execute.return_value = ListRequestsOutput(requests=[])
        scheduler.format_request_status_response.return_value = {"requests": []}

        args = _make_namespace()

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            result = await handle_list_requests(args)

        list_req_orch.execute.assert_awaited_once()
        assert isinstance(result, dict)
