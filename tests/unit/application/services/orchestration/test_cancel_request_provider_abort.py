"""Cancelling a request aborts provider-side provisioning before returning machines."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.commands import CancelRequestCommand
from orb.application.dto.queries import GetMachineQuery, SyncAndGetRequestQuery
from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
from orb.application.services.orchestration.dtos import (
    CancelRequestInput,
    ReturnMachinesOutput,
)
from orb.domain.base.exceptions import ApplicationError, ProviderContractError
from orb.domain.base.operations import OperationType

pytestmark = [pytest.mark.unit, pytest.mark.application]


def _request(
    status: str = "in_progress",
    machine_ids: Optional[list[str]] = None,
    resource_ids: Optional[list[str]] = None,
    provider_name: str = "aws-1",
    provider_api: str = "EC2Fleet",
):
    return SimpleNamespace(
        status=status,
        machine_ids=machine_ids or [],
        resource_ids=["fleet-1"] if resource_ids is None else resource_ids,
        provider_name=provider_name,
        provider_api=provider_api,
    )


def _provider_result(resources: list[dict], success: bool = True, error: Optional[str] = None):
    return SimpleNamespace(
        success=success,
        data={"resources": resources},
        error_message=error,
    )


class _Harness:
    def __init__(
        self,
        request: Any,
        provider_result: Any = None,
        supports: bool = True,
        flip_cancelled: bool = True,
        known_machines: tuple[str, ...] = ("i-1", "i-2", "i-late"),
    ) -> None:
        self.calls: list[str] = []
        self.command_bus = MagicMock()

        async def run_command(command: Any) -> None:
            if isinstance(command, CancelRequestCommand):
                self.calls.append("flip")
                command.cancelled = flip_cancelled
                command.final_status = "cancelled" if flip_cancelled else request.status

        self.command_bus.execute = AsyncMock(side_effect=run_command)

        self.query_bus = MagicMock()
        self.queries: list[Any] = []

        async def run_query(query: Any) -> Any:
            self.queries.append(query)
            if isinstance(query, GetMachineQuery):
                return (
                    SimpleNamespace(machine_id=query.machine_id)
                    if (query.machine_id in known_machines)
                    else None
                )
            if query.request_id == "ret-1":
                return SimpleNamespace(status="complete")
            return request

        self.query_bus.execute = AsyncMock(side_effect=run_query)

        self.return_orchestrator = MagicMock()

        async def run_return(return_input: Any) -> Any:
            self.calls.append("return")
            return ReturnMachinesOutput(request_id="ret-1", status="complete", message="ok")

        self.return_orchestrator.execute = AsyncMock(side_effect=run_return)

        self.provider = MagicMock()
        capabilities = MagicMock()
        capabilities.supports_operation.side_effect = lambda op: (
            supports and op == OperationType.CANCEL_PROVISIONING
        )
        self.provider.get_strategy_capabilities.return_value = capabilities
        self.operations: list[Any] = []

        async def run_operation(provider_id: str, operation: Any) -> Any:
            self.calls.append("abort")
            self.operations.append((provider_id, operation))
            if isinstance(provider_result, Exception):
                raise provider_result
            return provider_result

        self.provider.execute_operation = AsyncMock(side_effect=run_operation)

        self.orchestrator = CancelRequestOrchestrator(
            command_bus=self.command_bus,
            query_bus=self.query_bus,
            return_orchestrator=self.return_orchestrator,
            logger=MagicMock(),
            provider_selection_port=self.provider,
        )


ABORTED = [{"resource_id": "fleet-1", "outcome": "aborted", "message": "", "members": []}]


class TestAbortHappensFirst:
    @pytest.mark.asyncio
    async def test_unfulfilled_request_with_no_machines_is_aborted_then_cancelled(self):
        h = _Harness(_request(), _provider_result(ABORTED))

        out = await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        assert h.calls == ["abort", "flip"]
        provider_id, operation = h.operations[0]
        assert provider_id == "aws-1"
        assert operation.operation_type == OperationType.CANCEL_PROVISIONING
        assert operation.parameters == {
            "request_id": "req-1",
            "provider_api": "EC2Fleet",
            "resource_ids": ["fleet-1"],
        }
        assert out.status == "cancelled"
        assert out.cancel_effective is True
        assert out.requests[0]["provider_abort"] == [
            {"resource_id": "fleet-1", "outcome": "aborted", "message": ""}
        ]

    @pytest.mark.asyncio
    async def test_partially_fulfilled_request_is_aborted_before_machines_are_returned(self):
        h = _Harness(
            _request(machine_ids=["i-1", "i-2"], status="partial_pending"),
            _provider_result(ABORTED),
        )

        await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        assert h.calls == ["abort", "return", "flip"]
        returned = h.return_orchestrator.execute.call_args[0][0].machine_ids
        assert sorted(returned) == ["i-1", "i-2"]

    @pytest.mark.asyncio
    async def test_listed_members_join_the_recorded_machines_without_duplicates(self):
        resources = [
            {
                "resource_id": "fleet-1",
                "outcome": "aborted",
                "message": "",
                "members": ["i-1", "i-late"],
            }
        ]
        h = _Harness(_request(machine_ids=["i-1"]), _provider_result(resources))

        await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        returned = h.return_orchestrator.execute.call_args[0][0].machine_ids
        assert sorted(returned) == ["i-1", "i-late"]

    @pytest.mark.asyncio
    async def test_the_request_is_not_synced_again_after_the_abort(self):
        """A second status-mutating sync could move an aborted fleet's request off cancel."""
        h = _Harness(_request(machine_ids=["i-1"]), _provider_result(ABORTED))

        await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        syncs = [
            q
            for q in h.queries
            if isinstance(q, SyncAndGetRequestQuery)
            and not q.lightweight
            and q.request_id == "req-1"
        ]
        assert len(syncs) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["partial", "timeout"])
    async def test_settled_partial_and_timeout_requests_are_aborted_and_report_true_status(
        self, status
    ):
        h = _Harness(
            _request(status=status, machine_ids=["i-1"]),
            _provider_result(ABORTED),
            flip_cancelled=False,
        )

        out = await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        assert h.calls == ["abort", "return", "flip"]
        assert out.status == status
        assert out.cancel_effective is True
        assert out.requests[0]["provider_abort"][0]["outcome"] == "aborted"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["complete", "failed"])
    async def test_settled_requests_are_not_aborted(self, status):
        h = _Harness(_request(status=status), _provider_result(ABORTED), flip_cancelled=False)

        out = await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        h.provider.execute_operation.assert_not_called()
        assert "provider_abort" not in out.requests[0]

    @pytest.mark.asyncio
    async def test_a_cancelled_request_with_recorded_resources_is_aborted_again(self):
        """An earlier abort may not have taken effect (throttle, open breaker)."""
        resources = [{"resource_id": "fleet-1", "outcome": "already_gone", "message": ""}]
        h = _Harness(
            _request(status="cancelled"), _provider_result(resources), flip_cancelled=False
        )

        out = await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        assert h.calls[0] == "abort"
        assert out.requests[0]["provider_abort"][0]["outcome"] == "already_gone"

    @pytest.mark.asyncio
    async def test_resources_recorded_after_the_cancel_loaded_the_request_are_aborted(self):
        first = _request(resource_ids=[])
        later = _request(status="cancelled", resource_ids=["fleet-late"])
        h = _Harness(first, _provider_result([]))
        answers = iter([first, later])

        async def run_query(query: Any) -> Any:
            if isinstance(query, GetMachineQuery):
                return None
            return next(answers)

        h.query_bus.execute = AsyncMock(side_effect=run_query)
        h.provider.execute_operation = AsyncMock(
            return_value=_provider_result(
                [{"resource_id": "fleet-late", "outcome": "aborted", "message": "", "members": []}]
            )
        )

        out = await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        operation = h.provider.execute_operation.await_args.args[1]
        assert operation.parameters["resource_ids"] == ["fleet-late"]
        assert out.requests[0]["provider_abort"][0]["resource_id"] == "fleet-late"

    @pytest.mark.asyncio
    async def test_a_request_put_back_in_progress_by_creation_is_cancelled_again(self):
        first = _request(resource_ids=[])
        later = _request(status="acquiring", resource_ids=["fleet-late"])
        h = _Harness(first, _provider_result([]))
        answers = iter([first, later])

        async def run_query(query: Any) -> Any:
            return next(answers)

        h.query_bus.execute = AsyncMock(side_effect=run_query)
        h.provider.execute_operation = AsyncMock(
            return_value=_provider_result(
                [{"resource_id": "fleet-late", "outcome": "aborted", "message": "", "members": []}]
            )
        )

        await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        flips = [c for c in h.command_bus.execute.await_args_list]
        assert len(flips) == 2

    @pytest.mark.asyncio
    async def test_request_without_resources_is_not_aborted(self):
        h = _Harness(_request(resource_ids=[]), _provider_result(ABORTED))

        await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        h.provider.execute_operation.assert_not_called()

    @pytest.mark.asyncio
    async def test_listed_instances_without_a_machine_record_are_left_to_the_abort(self):
        resources = [
            {
                "resource_id": "fleet-1",
                "outcome": "aborted",
                "message": "",
                "members": ["i-1", "i-unrecorded"],
            }
        ]
        h = _Harness(_request(machine_ids=["i-1"]), _provider_result(resources))

        out = await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        returned = h.return_orchestrator.execute.call_args[0][0].machine_ids
        assert returned == ["i-1"]
        assert out.status == "cancelled"

    @pytest.mark.asyncio
    async def test_only_unrecorded_instances_listed_means_no_return_is_needed(self):
        resources = [
            {
                "resource_id": "fleet-1",
                "outcome": "aborted",
                "message": "",
                "members": ["i-unrecorded"],
            }
        ]
        h = _Harness(_request(), _provider_result(resources))

        out = await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        h.return_orchestrator.execute.assert_not_called()
        assert h.calls == ["abort", "flip"]
        assert out.status == "cancelled"


class TestAbortFailures:
    @pytest.mark.asyncio
    async def test_failed_abort_raises_and_leaves_the_request_unchanged(self):
        failed = [{"resource_id": "fleet-1", "outcome": "failed", "message": "denied"}]
        h = _Harness(
            _request(machine_ids=["i-1"]),
            _provider_result(failed, success=False, error="Failed to abort 1 of 1 resource(s)"),
        )

        with pytest.raises(ApplicationError, match="launching"):
            await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        assert h.calls == ["abort"]
        h.command_bus.execute.assert_not_called()
        h.return_orchestrator.execute.assert_not_called()
        logger = h.orchestrator._logger
        events = [c.kwargs.get("extra", {}).get("event") for c in logger.error.call_args_list]
        assert "request.cancel_failed" in events

    @pytest.mark.asyncio
    async def test_provider_exception_raises_cancel_failed(self):
        h = _Harness(_request(), RuntimeError("provider down"))

        with pytest.raises(ApplicationError, match="provider down"):
            await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        h.command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_retry_after_a_partial_failure_treats_done_resources_as_already_gone(self):
        request = _request(resource_ids=["fleet-a", "fleet-b"])
        first = _provider_result(
            [
                {"resource_id": "fleet-a", "outcome": "aborted", "message": ""},
                {"resource_id": "fleet-b", "outcome": "failed", "message": "throttled"},
            ],
            success=False,
            error="Failed to abort 1 of 2 resource(s)",
        )
        h = _Harness(request, first)
        with pytest.raises(ApplicationError):
            await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))
        h.command_bus.execute.assert_not_called()

        h.provider.execute_operation = AsyncMock(
            return_value=_provider_result(
                [
                    {"resource_id": "fleet-a", "outcome": "already_gone", "message": ""},
                    {"resource_id": "fleet-b", "outcome": "aborted", "message": ""},
                ]
            )
        )
        out = await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        assert out.status == "cancelled"
        outcomes = {o["resource_id"]: o["outcome"] for o in out.requests[0]["provider_abort"]}
        assert outcomes == {"fleet-a": "already_gone", "fleet-b": "aborted"}


class TestUnsupportedProviders:
    @pytest.mark.asyncio
    async def test_provider_without_the_operation_is_recorded_as_unsupported_and_cancel_proceeds(
        self,
    ):
        h = _Harness(_request(machine_ids=["i-1"]), _provider_result(ABORTED), supports=False)

        out = await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        h.provider.execute_operation.assert_not_called()
        assert h.calls == ["return", "flip"]
        assert out.requests[0]["provider_abort"][0]["outcome"] == "unsupported"
        assert out.status == "cancelled"

    @pytest.mark.asyncio
    async def test_unsupported_provider_does_not_make_a_settled_request_effective(self):
        h = _Harness(
            _request(status="partial"),
            _provider_result(ABORTED),
            supports=False,
            flip_cancelled=False,
        )

        out = await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        assert out.status == "partial"
        assert out.cancel_effective is False

    @pytest.mark.asyncio
    async def test_request_without_a_provider_name_is_recorded_as_unsupported(self):
        h = _Harness(_request(provider_name=""), _provider_result(ABORTED))

        out = await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        h.provider.execute_operation.assert_not_called()
        assert out.requests[0]["provider_abort"][0]["outcome"] == "unsupported"

    @pytest.mark.asyncio
    async def test_orchestrator_without_a_provider_port_skips_the_abort(self):
        h = _Harness(_request(), _provider_result(ABORTED))
        orchestrator = CancelRequestOrchestrator(
            command_bus=h.command_bus,
            query_bus=h.query_bus,
            return_orchestrator=h.return_orchestrator,
            logger=MagicMock(),
        )

        out = await orchestrator.execute(CancelRequestInput(request_id="req-1"))

        assert out.requests[0]["provider_abort"][0]["outcome"] == "unsupported"


class TestCancelWhileCreationIsInFlight:
    @pytest.mark.asyncio
    async def test_a_request_with_no_provider_status_yet_is_cancelled_from_its_stored_state(self):
        """Sync raises ProviderContractError for a pending request that has no resources."""
        pending = _request(status="pending", resource_ids=[])
        h = _Harness(pending, _provider_result([]))
        calls: list[bool] = []

        async def run_query(query: Any) -> Any:
            calls.append(query.lightweight)
            if not query.lightweight:
                raise ProviderContractError("did not emit ProviderFulfilment for acquire request")
            return pending

        h.query_bus.execute = AsyncMock(side_effect=run_query)

        out = await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))

        assert calls[:2] == [False, True]
        assert any(
            "no provider status yet" in str(c)
            for c in h.orchestrator._logger.warning.call_args_list
        )
        assert h.calls == ["flip"]
        assert out.status == "cancelled"

    @pytest.mark.asyncio
    async def test_other_load_failures_still_fail_the_cancel(self):
        h = _Harness(_request(), _provider_result([]))
        h.query_bus.execute = AsyncMock(side_effect=RuntimeError("store down"))

        with pytest.raises(ApplicationError, match="could not load"):
            await h.orchestrator.execute(CancelRequestInput(request_id="req-1"))
