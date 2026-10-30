"""Unit tests for CancelRequestOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.commands import CancelRequestCommand
from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
from orb.application.services.orchestration.dtos import (
    CancelRequestInput,
    CancelRequestOutput,
    ReturnMachinesOutput,
)
from orb.domain.base.exceptions import ApplicationError


@pytest.fixture
def mock_command_bus():
    bus = MagicMock()
    bus.execute = AsyncMock()
    return bus


@pytest.fixture
def mock_query_bus():
    bus = MagicMock()
    # Default: query bus returns a request with no machine_ids so the
    # orchestrator skips the return-then-cancel path. Individual tests
    # override this for the machines-allocated cases.
    bus.execute = AsyncMock(return_value=MagicMock(machine_ids=[]))
    return bus


@pytest.fixture
def mock_return_orchestrator():
    orch = MagicMock()
    orch.execute = AsyncMock(
        return_value=ReturnMachinesOutput(
            request_id="ret-001",
            status="complete",
            message="Returned",
        )
    )
    return orch


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def orchestrator(mock_command_bus, mock_query_bus, mock_return_orchestrator, mock_logger):
    return CancelRequestOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        return_orchestrator=mock_return_orchestrator,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestCancelRequestOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_cancel_request_command(self, orchestrator, mock_command_bus):
        input = CancelRequestInput(request_id="req-001", reason="no longer needed")
        await orchestrator.execute(input)
        mock_command_bus.execute.assert_called_once()
        cmd = mock_command_bus.execute.call_args[0][0]
        assert isinstance(cmd, CancelRequestCommand)
        assert cmd.request_id == "req-001"
        assert cmd.reason == "no longer needed"

    @pytest.mark.asyncio
    async def test_execute_returns_cancelled_status(self, orchestrator):
        input = CancelRequestInput(request_id="req-001")
        result = await orchestrator.execute(input)
        assert isinstance(result, CancelRequestOutput)
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_execute_returns_correct_request_id(self, orchestrator):
        input = CancelRequestInput(request_id="req-abc")
        result = await orchestrator.execute(input)
        assert result.request_id == "req-abc"

    @pytest.mark.asyncio
    async def test_execute_raw_contains_request_id_and_status(self, orchestrator):
        input = CancelRequestInput(request_id="req-xyz")
        result = await orchestrator.execute(input)
        assert result.request_id == "req-xyz"
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_execute_uses_default_reason(self, orchestrator, mock_command_bus):
        input = CancelRequestInput(request_id="req-001")
        await orchestrator.execute(input)
        cmd = mock_command_bus.execute.call_args[0][0]
        assert cmd.reason == "Cancelled via API"

    @pytest.mark.asyncio
    async def test_execute_calls_query_bus_to_collect_machine_ids(
        self, orchestrator, mock_query_bus
    ):
        """Cancel now needs the request's machine_ids, so it consults the query bus."""
        input = CancelRequestInput(request_id="req-001")
        await orchestrator.execute(input)
        mock_query_bus.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_skips_return_when_no_machines(
        self, orchestrator, mock_return_orchestrator
    ):
        """No machines allocated → return orchestrator is never invoked."""
        input = CancelRequestInput(request_id="req-001")
        await orchestrator.execute(input)
        mock_return_orchestrator.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_dispatches_return_when_machines_exist(
        self, mock_command_bus, mock_logger, mock_return_orchestrator
    ):
        """Machines allocated → return orchestrator is invoked with their IDs first."""
        query_bus = MagicMock()
        query_bus.execute = AsyncMock(return_value=MagicMock(machine_ids=["i-aaa", "i-bbb"]))
        orchestrator = CancelRequestOrchestrator(
            command_bus=mock_command_bus,
            query_bus=query_bus,
            return_orchestrator=mock_return_orchestrator,
            logger=mock_logger,
        )
        result = await orchestrator.execute(CancelRequestInput(request_id="req-001"))

        mock_return_orchestrator.execute.assert_called_once()
        return_input = mock_return_orchestrator.execute.call_args[0][0]
        assert sorted(return_input.machine_ids) == ["i-aaa", "i-bbb"]
        # Cancel still flips status after the return.
        assert result.status == "cancelled"

    @staticmethod
    def _with_machines(mock_command_bus, mock_logger, return_orchestrator, ids=("i-aaa",)):
        query_bus = MagicMock()
        query_bus.execute = AsyncMock(return_value=MagicMock(machine_ids=list(ids)))
        return CancelRequestOrchestrator(
            command_bus=mock_command_bus,
            query_bus=query_bus,
            return_orchestrator=return_orchestrator,
            logger=mock_logger,
        )

    @pytest.mark.asyncio
    async def test_return_exception_blocks_cancel(
        self, mock_command_bus, mock_logger, mock_return_orchestrator
    ):
        """A raising return must not flip the request while machines may still run."""
        mock_return_orchestrator.execute = AsyncMock(side_effect=Exception("aws boom"))
        orchestrator = self._with_machines(mock_command_bus, mock_logger, mock_return_orchestrator)
        with pytest.raises(ApplicationError, match="aws boom"):
            await orchestrator.execute(CancelRequestInput(request_id="req-001"))
        mock_command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["failed", "partial", "timeout", "error"])
    async def test_failed_return_request_blocks_cancel(
        self, mock_command_bus, mock_logger, mock_return_orchestrator, status
    ):
        """The orchestrator reports "pending" without waiting; the stored outcome decides."""
        mock_return_orchestrator.execute = AsyncMock(
            return_value=ReturnMachinesOutput(
                request_id="ret-001", status="pending", request_ids=["ret-001"]
            )
        )
        orchestrator = self._with_machines(mock_command_bus, mock_logger, mock_return_orchestrator)
        orchestrator._query_bus.execute = AsyncMock(
            side_effect=[MagicMock(machine_ids=["i-aaa"]), MagicMock(status=status)]
        )
        with pytest.raises(ApplicationError, match="may still be running"):
            await orchestrator.execute(CancelRequestInput(request_id="req-001"))
        mock_command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_any_failed_return_request_among_several_blocks_cancel(
        self, mock_command_bus, mock_logger, mock_return_orchestrator
    ):
        mock_return_orchestrator.execute = AsyncMock(
            return_value=ReturnMachinesOutput(
                request_id="ret-1", status="pending", request_ids=["ret-1", "ret-2"]
            )
        )
        orchestrator = self._with_machines(mock_command_bus, mock_logger, mock_return_orchestrator)
        orchestrator._query_bus.execute = AsyncMock(
            side_effect=[
                MagicMock(machine_ids=["i-aaa"]),
                MagicMock(status="in_progress"),
                MagicMock(status="failed"),
            ]
        )
        with pytest.raises(ApplicationError):
            await orchestrator.execute(CancelRequestInput(request_id="req-001"))
        mock_command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_unreadable_return_request_blocks_cancel(
        self, mock_command_bus, mock_logger, mock_return_orchestrator
    ):
        mock_return_orchestrator.execute = AsyncMock(
            return_value=ReturnMachinesOutput(
                request_id="ret-1", status="pending", request_ids=["ret-1"]
            )
        )
        orchestrator = self._with_machines(mock_command_bus, mock_logger, mock_return_orchestrator)
        orchestrator._query_bus.execute = AsyncMock(
            side_effect=[MagicMock(machine_ids=["i-aaa"]), RuntimeError("db down")]
        )
        with pytest.raises(ApplicationError):
            await orchestrator.execute(CancelRequestInput(request_id="req-001"))
        mock_command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_accepted_termination_flips_state(
        self, mock_command_bus, mock_logger, mock_return_orchestrator
    ):
        mock_return_orchestrator.execute = AsyncMock(
            return_value=ReturnMachinesOutput(
                request_id="ret-1", status="pending", request_ids=["ret-1"]
            )
        )
        orchestrator = self._with_machines(mock_command_bus, mock_logger, mock_return_orchestrator)
        orchestrator._query_bus.execute = AsyncMock(
            side_effect=[MagicMock(machine_ids=["i-aaa"]), MagicMock(status="in_progress")]
        )
        result = await orchestrator.execute(CancelRequestInput(request_id="req-001"))
        mock_command_bus.execute.assert_called_once()
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["no_op", "no_machines", "pending", "completed", "complete"])
    async def test_non_blocking_return_status_flips_state(
        self, mock_command_bus, mock_logger, mock_return_orchestrator, status
    ):
        mock_return_orchestrator.execute = AsyncMock(
            return_value=ReturnMachinesOutput(request_id="ret-001", status=status, message="ok")
        )
        orchestrator = self._with_machines(mock_command_bus, mock_logger, mock_return_orchestrator)
        result = await orchestrator.execute(CancelRequestInput(request_id="req-001"))
        mock_command_bus.execute.assert_called_once()
        assert result.requests[0]["return_status"] == status

    @pytest.mark.asyncio
    async def test_query_failure_does_not_fall_back_to_state_only_cancel(
        self, mock_command_bus, mock_query_bus, mock_return_orchestrator, mock_logger
    ):
        mock_query_bus.execute.side_effect = RuntimeError("db down")
        orchestrator = CancelRequestOrchestrator(
            command_bus=mock_command_bus,
            query_bus=mock_query_bus,
            return_orchestrator=mock_return_orchestrator,
            logger=mock_logger,
        )
        with pytest.raises(ApplicationError, match="db down"):
            await orchestrator.execute(CancelRequestInput(request_id="req-001"))
        mock_command_bus.execute.assert_not_called()
        mock_return_orchestrator.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_request_does_not_fall_back_to_state_only_cancel(
        self, mock_command_bus, mock_query_bus, mock_return_orchestrator, mock_logger
    ):
        mock_query_bus.execute.return_value = None
        orchestrator = CancelRequestOrchestrator(
            command_bus=mock_command_bus,
            query_bus=mock_query_bus,
            return_orchestrator=mock_return_orchestrator,
            logger=mock_logger,
        )
        with pytest.raises(ApplicationError):
            await orchestrator.execute(CancelRequestInput(request_id="req-001"))
        mock_command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_command_bus_error_propagates(self, orchestrator, mock_command_bus):
        mock_command_bus.execute.side_effect = Exception("bus failure")
        input = CancelRequestInput(request_id="req-001")
        with pytest.raises(Exception, match="bus failure"):
            await orchestrator.execute(input)


def _alerts(logger: MagicMock) -> list[dict]:
    return [
        c.kwargs["extra"]
        for c in logger.error.call_args_list
        if c.kwargs.get("extra", {}).get("event") == "request.cancel_failed"
    ]


@pytest.mark.unit
@pytest.mark.application
class TestCancelFailureAuditRecord:
    """Every failed cancel emits one structured, alertable record."""

    @staticmethod
    def _orchestrator(command_bus, logger, return_orchestrator, request):
        query_bus = MagicMock()
        query_bus.execute = AsyncMock(return_value=request)
        return CancelRequestOrchestrator(
            command_bus=command_bus,
            query_bus=query_bus,
            return_orchestrator=return_orchestrator,
            logger=logger,
        )

    @staticmethod
    def _request():
        return MagicMock(
            machine_ids=["i-aaa"],
            provider_name="aws-prod",
            provider_type="aws",
            provider_api="RunInstances",
            resource_id="r-123",
        )

    @pytest.mark.asyncio
    async def test_return_exception_emits_alert_with_provider_context(
        self, mock_command_bus, mock_logger, mock_return_orchestrator
    ):
        mock_return_orchestrator.execute = AsyncMock(side_effect=Exception("aws boom"))
        orch = self._orchestrator(
            mock_command_bus, mock_logger, mock_return_orchestrator, self._request()
        )

        with pytest.raises(ApplicationError):
            await orch.execute(CancelRequestInput(request_id="req-9"))

        (alert,) = _alerts(mock_logger)
        assert alert["request_id"] == "req-9"
        assert "aws boom" in alert["reason"]
        assert alert["provider_name"] == "aws-prod"
        assert alert["provider_type"] == "aws"
        assert alert["provider_api"] == "RunInstances"
        assert alert["resource_id"] == "r-123"

    @pytest.mark.asyncio
    async def test_failed_return_request_emits_alert(
        self, mock_command_bus, mock_logger, mock_return_orchestrator
    ):
        mock_return_orchestrator.execute = AsyncMock(
            return_value=ReturnMachinesOutput(
                request_id="ret-1", status="pending", request_ids=["ret-1"]
            )
        )
        orch = self._orchestrator(
            mock_command_bus, mock_logger, mock_return_orchestrator, self._request()
        )
        orch._query_bus.execute = AsyncMock(
            side_effect=[self._request(), MagicMock(status="failed")]
        )

        with pytest.raises(ApplicationError):
            await orch.execute(CancelRequestInput(request_id="req-9"))

        (alert,) = _alerts(mock_logger)
        assert "failed" in alert["reason"]

    @pytest.mark.asyncio
    async def test_unloadable_request_emits_alert(
        self, mock_command_bus, mock_logger, mock_return_orchestrator
    ):
        orch = self._orchestrator(mock_command_bus, mock_logger, mock_return_orchestrator, None)

        with pytest.raises(ApplicationError):
            await orch.execute(CancelRequestInput(request_id="req-9"))

        (alert,) = _alerts(mock_logger)
        assert alert["request_id"] == "req-9"

    @pytest.mark.asyncio
    async def test_successful_cancel_emits_no_alert(
        self, mock_command_bus, mock_logger, mock_return_orchestrator
    ):
        request = MagicMock(machine_ids=[])
        orch = self._orchestrator(mock_command_bus, mock_logger, mock_return_orchestrator, request)

        await orch.execute(CancelRequestInput(request_id="req-9"))

        assert _alerts(mock_logger) == []
