"""Cancel must not flip a request while the real return flow failed to terminate.

Uses the real ReturnMachinesOrchestrator and the real return-request handler
deprovisioning path; only the buses, stores and the provider call are faked.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.commands.request_creation_handlers import CreateReturnRequestHandler
from orb.application.dto.commands import (
    CancelRequestCommand,
    CreateReturnRequestCommand,
    UpdateRequestStatusCommand,
)
from orb.application.dto.queries import SyncAndGetRequestQuery
from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
from orb.application.services.orchestration.dtos import CancelRequestInput
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
from orb.domain.base.exceptions import ApplicationError
from orb.domain.request.request_types import RequestStatus


def _return_handler(deprovision_result: dict[str, Any], statuses: dict[str, str]):
    machine = MagicMock()
    machine.machine_id.value = "i-aaa"
    machine.update_status.return_value = machine
    machine.model_copy.return_value = machine
    uow = MagicMock()
    uow.machines.get_by_id.return_value = machine

    @contextmanager
    def _create():
        yield uow

    uow_factory = MagicMock()
    uow_factory.create_unit_of_work.side_effect = _create

    inner_bus = MagicMock()

    async def record_status(command: Any) -> None:
        if isinstance(command, UpdateRequestStatusCommand):
            statuses[command.request_id] = command.status.value

    inner_bus.execute = AsyncMock(side_effect=record_status)
    container = MagicMock()
    container.get = MagicMock(return_value=inner_bus)

    handler = CreateReturnRequestHandler(
        uow_factory=uow_factory,
        logger=MagicMock(),
        container=container,
        event_publisher=MagicMock(),
        error_handler=MagicMock(),
        query_bus=MagicMock(),
        provider_selection_port=MagicMock(),
    )
    handler._deprovisioning_orchestrator.execute_deprovisioning = AsyncMock(
        return_value=deprovision_result
    )
    handler._machine_grouping_service.group_by_resource = MagicMock(
        return_value=({("aws", "RunInstances", "r-1"): [MagicMock()]}, [])
    )
    return handler


def _build(deprovision_result: dict[str, Any]):
    statuses: dict[str, str] = {}
    handler = _return_handler(deprovision_result, statuses)

    return_command_bus = MagicMock()

    async def run_return(command: Any) -> None:
        assert isinstance(command, CreateReturnRequestCommand)
        request = MagicMock()
        request.request_id = "ret-1"
        command.created_request_ids = ["ret-1"]
        await handler._execute_deprovisioning_for_request(["i-aaa"], request, "aws")

    return_command_bus.execute = AsyncMock(side_effect=run_return)

    async def query(q: Any) -> Any:
        assert isinstance(q, SyncAndGetRequestQuery)
        if q.request_id == "req-acquire":
            return MagicMock(machine_ids=["i-aaa"])
        return MagicMock(status=statuses[q.request_id])

    query_bus = MagicMock()
    query_bus.execute = AsyncMock(side_effect=query)

    real_return = ReturnMachinesOrchestrator(return_command_bus, query_bus, MagicMock())
    cancel_bus = MagicMock()
    cancel_bus.execute = AsyncMock()
    orchestrator = CancelRequestOrchestrator(cancel_bus, query_bus, real_return, MagicMock())
    return orchestrator, cancel_bus, statuses


@pytest.mark.unit
@pytest.mark.application
class TestCancelWithRealReturnFlow:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "result",
        [
            {"success": False, "errors": ["TerminateInstances denied"]},
            {
                "success": False,
                "partial": True,
                "successful_instance_ids": [],
                "failed_instance_ids": ["i-aaa"],
            },
        ],
    )
    async def test_failed_termination_does_not_cancel_request(self, result):
        orchestrator, cancel_bus, statuses = _build(result)

        with pytest.raises(ApplicationError, match="may still be running"):
            await orchestrator.execute(CancelRequestInput(request_id="req-acquire"))

        assert statuses["ret-1"] in ("failed", "partial")
        dispatched = [c.args[0] for c in cancel_bus.execute.call_args_list]
        assert not any(isinstance(c, CancelRequestCommand) for c in dispatched)

    @pytest.mark.asyncio
    async def test_accepted_termination_cancels_request(self):
        orchestrator, cancel_bus, statuses = _build({"success": True})

        result = await orchestrator.execute(CancelRequestInput(request_id="req-acquire"))

        assert result.status == "cancelled"
        assert statuses["ret-1"] not in ("failed", "partial")
        cancel_bus.execute.assert_called_once()


class TestCancelWhenFailureCannotBeRecorded:
    """If the failed status cannot be stored the request still reads IN_PROGRESS."""

    @staticmethod
    def _broken_status_store(handler: CreateReturnRequestHandler, statuses: dict[str, str]):
        inner_bus = handler._container.get.return_value

        async def record(command: Any) -> None:
            if isinstance(command, UpdateRequestStatusCommand):
                if command.status.value == "failed":
                    raise RuntimeError("status store down")
                statuses[command.request_id] = command.status.value

        inner_bus.execute = AsyncMock(side_effect=record)
        # The direct write fallback fails as well.
        handler.uow_factory.create_unit_of_work.side_effect = None
        uow = MagicMock()
        uow.requests.get_by_id.side_effect = RuntimeError("db down")
        uow.machines.get_by_id.return_value = MagicMock()

        @contextmanager
        def _create():
            yield uow

        handler.uow_factory.create_unit_of_work.side_effect = _create

    @pytest.mark.asyncio
    async def test_status_update_double_failure_raises_with_alert_record(self):
        statuses: dict[str, str] = {}
        handler = _return_handler({"success": False, "errors": ["denied"]}, statuses)
        self._broken_status_store(handler, statuses)
        request = MagicMock()
        request.request_id = "ret-1"

        with pytest.raises(ApplicationError, match="could not be recorded"):
            await handler._update_request_status(request, RequestStatus.FAILED, "m")

        extra = handler.logger.error.call_args_list[-1].kwargs["extra"]
        assert extra["event"] == "return.status_update_failed"
        assert extra["request_id"] == "ret-1"

    @pytest.mark.asyncio
    async def test_cancel_is_blocked_when_failure_cannot_be_recorded(self):
        statuses: dict[str, str] = {}
        handler = _return_handler({"success": False, "errors": ["denied"]}, statuses)
        self._broken_status_store(handler, statuses)
        handler._machine_grouping_service.group_by_provider = MagicMock(
            return_value={("aws", "aws-prod", "RunInstances"): ["i-aaa"]}
        )
        handler._cancel_validate_and_persist = MagicMock()
        handler._container.get = MagicMock(
            side_effect=lambda key: (
                MagicMock(get_return_request_prefix=MagicMock(return_value="ret-"))
                if key.__name__ == "DomainConfigurationService"
                else handler._container.get.bus
            )
        )
        handler._container.get.bus = handler_bus = MagicMock()

        async def record(command: Any) -> None:
            if isinstance(command, UpdateRequestStatusCommand):
                if command.status.value == "failed":
                    raise RuntimeError("status store down")
                statuses[command.request_id] = command.status.value

        handler_bus.execute = AsyncMock(side_effect=record)

        return_command_bus = MagicMock()
        return_command_bus.execute = AsyncMock(side_effect=handler.execute_command)

        async def query(q: Any) -> Any:
            if q.request_id == "req-acquire":
                return MagicMock(machine_ids=["i-aaa"])
            return MagicMock(status=statuses.get(q.request_id, "in_progress"))

        query_bus = MagicMock()
        query_bus.execute = AsyncMock(side_effect=query)
        real_return = ReturnMachinesOrchestrator(return_command_bus, query_bus, MagicMock())
        cancel_bus = MagicMock()
        cancel_bus.execute = AsyncMock()
        orchestrator = CancelRequestOrchestrator(cancel_bus, query_bus, real_return, MagicMock())

        with pytest.raises(ApplicationError, match="may still be running"):
            await orchestrator.execute(CancelRequestInput(request_id="req-acquire"))

        cancel_bus.execute.assert_not_called()
