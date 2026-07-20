"""Unit tests for request_command_handlers.

Covers happy path and error/branch paths for all handlers.
"""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.orchestration.dtos import (
    AcquireMachinesOutput,
    CancelRequestOutput,
    GetRequestStatusOutput,
    ListRequestsOutput,
    ListReturnRequestsOutput,
    ReturnMachinesOutput,
)
from orb.interface.request_command_handlers import (
    handle_cancel_request,
    handle_get_multiple_requests,
    handle_get_request_status,
    handle_get_return_requests,
    handle_list_requests,
    handle_request_machines,
    handle_request_return_machines,
)
from orb.interface.response_formatting_service import ResponseFormattingService


def _make_formatter() -> MagicMock:
    fmt = MagicMock(spec=ResponseFormattingService)
    fmt.format_request_status.return_value = InterfaceResponse(data={"requests": []})
    fmt.format_request_operation.return_value = InterfaceResponse(data={"ok": True})
    fmt.format_return_requests.return_value = InterfaceResponse(data={"returns": []})
    fmt.format_error.return_value = InterfaceResponse(data={"error": "err"}, exit_code=1)
    return fmt


@pytest.mark.unit
class TestHandleGetRequestStatus:
    """Tests for handle_get_request_status."""

    @pytest.mark.asyncio
    async def test_all_and_specific_ids_returns_error(self):
        container = MagicMock()
        args = Namespace(
            _container=container,
            all=True,
            request_ids=["r1"],
            flag_request_ids=None,
        )
        result = await handle_get_request_status(args)

        assert isinstance(result, dict)
        assert "Cannot use --all" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_all_flag_calls_orchestrator_with_all_requests(self):
        from orb.application.services.orchestration.get_request_status import (
            GetRequestStatusOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=GetRequestStatusOrchestrator)
        orch.execute.return_value = GetRequestStatusOutput(requests=[])

        container = MagicMock()
        container.get.side_effect = lambda t: {
            GetRequestStatusOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            all=True,
            request_ids=None,
            flag_request_ids=None,
            verbose=False,
        )
        result = await handle_get_request_status(args)

        orch.execute.assert_awaited_once()
        call_input = orch.execute.call_args[0][0]
        assert call_input.all_requests is True
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_no_ids_returns_error_dict(self):
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.get_request_status import (
            GetRequestStatusOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=GetRequestStatusOrchestrator)
        scheduler = MagicMock(spec=SchedulerPort)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            GetRequestStatusOrchestrator: orch,
            ResponseFormattingService: fmt,
            SchedulerPort: scheduler,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            all=False,
            request_ids=None,
            flag_request_ids=None,
        )
        result = await handle_get_request_status(args)

        assert isinstance(result, dict)
        assert "No request ID" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_specific_ids_calls_orchestrator(self):
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.get_request_status import (
            GetRequestStatusOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=GetRequestStatusOrchestrator)
        orch.execute.return_value = GetRequestStatusOutput(requests=[{"request_id": "r-1"}])
        scheduler = MagicMock(spec=SchedulerPort)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            GetRequestStatusOrchestrator: orch,
            ResponseFormattingService: fmt,
            SchedulerPort: scheduler,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            all=False,
            request_ids=["r-1"],
            flag_request_ids=None,
            verbose=False,
        )
        result = await handle_get_request_status(args)

        orch.execute.assert_awaited_once()
        call_input = orch.execute.call_args[0][0]
        assert "r-1" in call_input.request_ids
        assert isinstance(result, InterfaceResponse)


@pytest.mark.unit
class TestHandleRequestMachines:
    """Tests for handle_request_machines."""

    @pytest.mark.asyncio
    async def test_no_template_id_returns_error(self):
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.acquire_machines import (
            AcquireMachinesOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=AcquireMachinesOrchestrator)
        scheduler = MagicMock(spec=SchedulerPort)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            AcquireMachinesOrchestrator: orch,
            ResponseFormattingService: fmt,
            SchedulerPort: scheduler,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            template_id=None,
            flag_template_id=None,
            machine_count=2,
            flag_machine_count=None,
        )
        result = await handle_request_machines(args)

        assert isinstance(result, dict)
        assert "Template ID is required" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_no_machine_count_returns_error(self):
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.acquire_machines import (
            AcquireMachinesOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=AcquireMachinesOrchestrator)
        scheduler = MagicMock(spec=SchedulerPort)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            AcquireMachinesOrchestrator: orch,
            ResponseFormattingService: fmt,
            SchedulerPort: scheduler,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            template_id="tmpl-1",
            flag_template_id=None,
            machine_count=0,
            flag_machine_count=None,
        )
        result = await handle_request_machines(args)

        assert isinstance(result, dict)
        assert "Machine count is required" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_happy_path(self):
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.acquire_machines import (
            AcquireMachinesOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=AcquireMachinesOrchestrator)
        orch.execute.return_value = AcquireMachinesOutput(
            request_id="req-1", status="pending", machine_ids=[]
        )
        scheduler = MagicMock(spec=SchedulerPort)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            AcquireMachinesOrchestrator: orch,
            ResponseFormattingService: fmt,
            SchedulerPort: scheduler,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            template_id="tmpl-1",
            flag_template_id=None,
            machine_count=3,
            flag_machine_count=None,
            wait=False,
            timeout=300,
        )
        result = await handle_request_machines(args)

        orch.execute.assert_awaited_once()
        call_input = orch.execute.call_args[0][0]
        assert call_input.template_id == "tmpl-1"
        assert call_input.requested_count == 3
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_input_data_parsed_by_scheduler(self):
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.acquire_machines import (
            AcquireMachinesOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=AcquireMachinesOrchestrator)
        orch.execute.return_value = AcquireMachinesOutput(
            request_id="req-2", status="pending", machine_ids=[]
        )
        scheduler = MagicMock(spec=SchedulerPort)
        scheduler.parse_request_data.return_value = {
            "template_id": "tmpl-from-file",
            "requested_count": 1,
        }

        container = MagicMock()
        container.get.side_effect = lambda t: {
            AcquireMachinesOrchestrator: orch,
            ResponseFormattingService: fmt,
            SchedulerPort: scheduler,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            input_data={"template_id": "tmpl-from-file", "requested_count": 1},
            wait=False,
            timeout=300,
        )
        result = await handle_request_machines(args)

        scheduler.parse_request_data.assert_called_once()
        orch.execute.assert_awaited_once()
        assert isinstance(result, InterfaceResponse)


@pytest.mark.unit
class TestHandleListRequests:
    """Tests for handle_list_requests."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=ListRequestsOrchestrator)
        orch.execute.return_value = ListRequestsOutput(requests=[], count=0, total_count=0)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ListRequestsOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            status=None,
            limit=None,
            sync=False,
            offset=None,
            template_id=None,
            request_type=None,
            provider_name=None,
            provider_type=None,
            filter=None,
        )
        result = await handle_list_requests(args)

        orch.execute.assert_awaited_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_filters_forwarded_to_orchestrator(self):
        from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=ListRequestsOrchestrator)
        orch.execute.return_value = ListRequestsOutput(requests=[], count=0, total_count=0)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ListRequestsOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            status="active",
            limit=10,
            sync=True,
            offset=5,
            template_id="tmpl-x",
            request_type="acquire",
            provider_name="aws-1",
            provider_type="aws",
            filter=["k=v"],
        )
        await handle_list_requests(args)

        call_input = orch.execute.call_args[0][0]
        assert call_input.status == "active"
        assert call_input.limit == 10
        assert call_input.sync is True
        assert call_input.offset == 5
        assert call_input.template_id == "tmpl-x"
        assert call_input.provider_name == "aws-1"


@pytest.mark.unit
class TestHandleCancelRequest:
    """Tests for handle_cancel_request."""

    @pytest.mark.asyncio
    async def test_no_request_id_returns_error(self):
        container = MagicMock()
        args = Namespace(_container=container, request_id=None, flag_request_id=None)
        result = await handle_cancel_request(args)

        assert isinstance(result, dict)
        assert "Request ID is required" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_happy_path(self):
        from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=CancelRequestOrchestrator)
        orch.execute.return_value = CancelRequestOutput(request_id="r-1", status="cancelled")

        container = MagicMock()
        container.get.side_effect = lambda t: {
            CancelRequestOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            request_id="r-1",
            flag_request_id=None,
            reason=None,
            force=False,
        )
        result = await handle_cancel_request(args)

        orch.execute.assert_awaited_once()
        call_input = orch.execute.call_args[0][0]
        assert call_input.request_id == "r-1"
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_flag_request_id_used_as_fallback(self):
        from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=CancelRequestOrchestrator)
        orch.execute.return_value = CancelRequestOutput(request_id="r-flag", status="cancelled")

        container = MagicMock()
        container.get.side_effect = lambda t: {
            CancelRequestOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            request_id=None,
            flag_request_id="r-flag",
            reason="user cancel",
            force=False,
        )
        result = await handle_cancel_request(args)

        call_input = orch.execute.call_args[0][0]
        assert call_input.request_id == "r-flag"
        assert call_input.reason == "user cancel"
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_default_reason_is_set(self):
        from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator

        fmt = _make_formatter()
        orch = AsyncMock(spec=CancelRequestOrchestrator)
        orch.execute.return_value = CancelRequestOutput(request_id="r-1", status="cancelled")

        container = MagicMock()
        container.get.side_effect = lambda t: {
            CancelRequestOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            request_id="r-1",
            flag_request_id=None,
            force=False,
        )
        await handle_cancel_request(args)

        call_input = orch.execute.call_args[0][0]
        assert call_input.reason == "Cancelled via API"


@pytest.mark.unit
class TestHandleGetReturnRequests:
    """Tests for handle_get_return_requests."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        from orb.application.services.orchestration.list_return_requests import (
            ListReturnRequestsOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=ListReturnRequestsOrchestrator)
        orch.execute.return_value = ListReturnRequestsOutput(requests=[], total_count=0)

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ListReturnRequestsOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            status=None,
            limit=50,
            offset=None,
            provider_name=None,
            provider_type=None,
            filter=None,
        )
        result = await handle_get_return_requests(args)

        orch.execute.assert_awaited_once()
        fmt.format_return_requests.assert_called_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_filters_forwarded(self):
        from orb.application.services.orchestration.list_return_requests import (
            ListReturnRequestsOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=ListReturnRequestsOrchestrator)
        orch.execute.return_value = ListReturnRequestsOutput(requests=[])

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ListReturnRequestsOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            status="pending",
            limit=10,
            offset=5,
            provider_name="k8s-1",
            provider_type="k8s",
            filter=None,
        )
        await handle_get_return_requests(args)

        call_input = orch.execute.call_args[0][0]
        assert call_input.status == "pending"
        assert call_input.limit == 10
        assert call_input.offset == 5
        assert call_input.provider_name == "k8s-1"


@pytest.mark.unit
class TestHandleRequestReturnMachines:
    """Tests for handle_request_return_machines."""

    @pytest.mark.asyncio
    async def test_request_id_and_machine_ids_returns_error(self):
        container = MagicMock()
        args = Namespace(
            _container=container,
            all=False,
            request_id="req-1",
            machine_ids=["m1"],
            machine_ids_flag=None,
        )
        result = await handle_request_return_machines(args)

        assert isinstance(result, dict)
        assert "Cannot use --request-id with specific machine IDs" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_request_id_and_all_returns_error(self):
        container = MagicMock()
        args = Namespace(
            _container=container,
            all=True,
            request_id="req-1",
            machine_ids=None,
            machine_ids_flag=None,
        )
        result = await handle_request_return_machines(args)

        assert isinstance(result, dict)
        assert "Cannot use --request-id with --all" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_all_and_machine_ids_returns_error(self):
        container = MagicMock()
        args = Namespace(
            _container=container,
            all=True,
            request_id=None,
            machine_ids=["m1"],
            machine_ids_flag=None,
        )
        result = await handle_request_return_machines(args)

        assert isinstance(result, dict)
        assert "Cannot use --all with specific machine IDs" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_all_without_force_returns_error(self):
        container = MagicMock()
        args = Namespace(
            _container=container,
            all=True,
            request_id=None,
            machine_ids=None,
            machine_ids_flag=None,
            force=False,
        )
        result = await handle_request_return_machines(args)

        assert isinstance(result, dict)
        assert "--force" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_no_ids_no_all_no_request_id_returns_error(self):
        container = MagicMock()
        args = Namespace(
            _container=container,
            all=False,
            request_id=None,
            machine_ids=None,
            machine_ids_flag=None,
        )
        result = await handle_request_return_machines(args)

        assert isinstance(result, dict)
        assert "Machine IDs are required" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_happy_path_specific_machine_ids(self):
        from orb.application.services.orchestration.return_machines import (
            ReturnMachinesOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=ReturnMachinesOrchestrator)
        orch.execute.return_value = ReturnMachinesOutput(
            request_id=None, status="returned", message="ok"
        )

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ReturnMachinesOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            all=False,
            request_id=None,
            machine_ids=["m1", "m2"],
            machine_ids_flag=None,
            force=False,
            wait=False,
            timeout=300,
            provider_name=None,
            provider_type=None,
        )
        result = await handle_request_return_machines(args)

        orch.execute.assert_awaited_once()
        call_input = orch.execute.call_args[0][0]
        assert "m1" in call_input.machine_ids
        assert isinstance(result, InterfaceResponse)


@pytest.mark.unit
class TestHandleGetMultipleRequests:
    """Tests for handle_get_multiple_requests."""

    @pytest.mark.asyncio
    async def test_no_ids_returns_error_dict(self):
        container = MagicMock()
        args = Namespace(
            _container=container,
            request_ids=None,
            flag_request_ids=None,
            flag_ids=None,
        )
        result = await handle_get_multiple_requests(args)

        assert isinstance(result, dict)
        assert "No request IDs provided" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_happy_path_via_query_bus(self):
        from orb.infrastructure.di.buses import QueryBus

        mock_bus = AsyncMock(spec=QueryBus)
        mock_result = MagicMock()
        mock_result.requests = []
        mock_result.found_count = 0
        mock_result.not_found_ids = []
        mock_result.total_requested = 2
        mock_bus.execute.return_value = mock_result

        container = MagicMock()
        container.get.side_effect = lambda t: {QueryBus: mock_bus}.get(t, MagicMock())

        args = Namespace(
            _container=container,
            request_ids=["r1", "r2"],
            flag_request_ids=None,
            flag_ids=None,
            include_machines=True,
        )
        result = await handle_get_multiple_requests(args)

        mock_bus.execute.assert_awaited_once()
        assert result["total_requested"] == 2
        assert "requests" in result

    @pytest.mark.asyncio
    async def test_flag_ids_merged(self):
        from orb.infrastructure.di.buses import QueryBus

        mock_bus = AsyncMock(spec=QueryBus)
        mock_result = MagicMock()
        mock_result.requests = []
        mock_result.found_count = 0
        mock_result.not_found_ids = []
        mock_result.total_requested = 1
        mock_bus.execute.return_value = mock_result

        container = MagicMock()
        container.get.side_effect = lambda t: {QueryBus: mock_bus}.get(t, MagicMock())

        args = Namespace(
            _container=container,
            request_ids=None,
            flag_request_ids=None,
            flag_ids=["r-via-flag"],
            include_machines=False,
        )
        await handle_get_multiple_requests(args)

        query_arg = mock_bus.execute.call_args[0][0]
        assert "r-via-flag" in query_arg.request_ids
