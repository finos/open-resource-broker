"""Unit tests for AcquireMachinesOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.dto.commands import CreateRequestCommand
from orb.application.dto.queries import GetRequestQuery
from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.dtos import AcquireMachinesInput, AcquireMachinesOutput
from orb.domain.base.exceptions import ApplicationError


@pytest.fixture
def mock_command_bus():
    bus = MagicMock()
    bus.execute = AsyncMock()
    return bus


@pytest.fixture
def mock_query_bus():
    bus = MagicMock()
    bus.execute = AsyncMock()
    return bus


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def orchestrator(mock_command_bus, mock_query_bus, mock_logger):
    return AcquireMachinesOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestAcquireMachinesOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_create_request_command(self, orchestrator, mock_command_bus):
        input = AcquireMachinesInput(template_id="tmpl-1", requested_count=2)
        await orchestrator.execute(input)
        mock_command_bus.execute.assert_called_once()
        cmd = mock_command_bus.execute.call_args[0][0]
        assert isinstance(cmd, CreateRequestCommand)
        assert cmd.template_id == "tmpl-1"
        assert cmd.requested_count == 2

    @pytest.mark.asyncio
    async def test_execute_wait_false_does_not_poll(self, orchestrator, mock_query_bus):
        input = AcquireMachinesInput(template_id="tmpl-1", requested_count=1, wait=False)
        await orchestrator.execute(input)
        mock_query_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_wait_false_returns_pending_status(self, orchestrator):
        input = AcquireMachinesInput(template_id="tmpl-1", requested_count=1, wait=False)
        result = await orchestrator.execute(input)
        assert isinstance(result, AcquireMachinesOutput)
        assert result.status == "pending"
        assert result.machine_ids == []

    @pytest.mark.asyncio
    async def test_execute_wait_true_polls_query_bus(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        # Arrange: command sets created_request_id via side_effect
        async def set_request_id(cmd):
            cmd.created_request_id = "req-123"

        mock_command_bus.execute.side_effect = set_request_id

        poll_result = MagicMock()
        poll_result.status = MagicMock()
        poll_result.status.value = "completed"
        poll_result.machine_references = []
        mock_query_bus.execute.return_value = poll_result

        input = AcquireMachinesInput(
            template_id="tmpl-1", requested_count=1, wait=True, timeout_seconds=10
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await orchestrator.execute(input)

        mock_query_bus.execute.assert_called()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, GetRequestQuery)
        assert query.request_id == "req-123"
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_execute_wait_true_returns_machine_ids(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        async def set_request_id(cmd):
            cmd.created_request_id = "req-456"

        mock_command_bus.execute.side_effect = set_request_id

        machine = MagicMock()
        machine.machine_id = "m-001"
        poll_result = MagicMock()
        poll_result.status = MagicMock()
        poll_result.status.value = "completed"
        poll_result.machine_references = [machine]
        mock_query_bus.execute.return_value = poll_result

        input = AcquireMachinesInput(
            template_id="tmpl-1", requested_count=1, wait=True, timeout_seconds=10
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await orchestrator.execute(input)

        assert result.machine_ids == ["m-001"]

    @pytest.mark.asyncio
    async def test_execute_wait_true_timeout_returns_timeout_status(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        async def set_request_id(cmd):
            cmd.created_request_id = "req-789"

        mock_command_bus.execute.side_effect = set_request_id

        # Always return non-terminal status
        poll_result = MagicMock()
        poll_result.status = MagicMock()
        poll_result.status.value = "pending"
        mock_query_bus.execute.return_value = poll_result

        input = AcquireMachinesInput(
            template_id="tmpl-1", requested_count=1, wait=True, timeout_seconds=4
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await orchestrator.execute(input)

        assert result.status == "timeout"
        assert result.machine_ids == []

    @pytest.mark.asyncio
    async def test_execute_poll_error_is_logged_and_continues(
        self, orchestrator, mock_command_bus, mock_query_bus, mock_logger
    ):
        async def set_request_id(cmd):
            cmd.created_request_id = "req-err"

        mock_command_bus.execute.side_effect = set_request_id

        # First call raises, second returns terminal
        terminal = MagicMock()
        terminal.status = MagicMock()
        terminal.status.value = "failed"
        terminal.machine_references = []
        mock_query_bus.execute.side_effect = [Exception("network error"), terminal]

        input = AcquireMachinesInput(
            template_id="tmpl-1", requested_count=1, wait=True, timeout_seconds=10
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await orchestrator.execute(input)

        mock_logger.warning.assert_called()
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_execute_output_has_correct_fields(self, orchestrator):
        input = AcquireMachinesInput(template_id="tmpl-1", requested_count=3)
        result = await orchestrator.execute(input)
        assert hasattr(result, "request_id")
        assert hasattr(result, "status")
        assert hasattr(result, "machine_ids")
        assert hasattr(result, "raw")

    @pytest.mark.asyncio
    async def test_wait_false_raw_contains_machine_ids_empty(self, orchestrator, mock_command_bus):
        async def set_request_id(cmd):
            cmd.created_request_id = "req-001"

        mock_command_bus.execute.side_effect = set_request_id

        result = await orchestrator.execute(
            AcquireMachinesInput(template_id="tmpl-1", requested_count=1, wait=False)
        )

        assert result.raw["request_id"] == "req-001"
        assert result.raw["status"] == "pending"
        assert result.raw["machine_ids"] == []

    @pytest.mark.asyncio
    async def test_wait_true_raw_contains_machine_ids(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        async def set_request_id(cmd):
            cmd.created_request_id = "req-002"

        mock_command_bus.execute.side_effect = set_request_id

        machine = MagicMock()
        machine.machine_id = "m-aaa"
        poll_result = MagicMock()
        poll_result.status = MagicMock()
        poll_result.status.value = "completed"
        poll_result.machine_references = [machine]
        mock_query_bus.execute.return_value = poll_result

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await orchestrator.execute(
                AcquireMachinesInput(
                    template_id="tmpl-1", requested_count=1, wait=True, timeout_seconds=10
                )
            )

        assert result.raw["machine_ids"] == ["m-aaa"]
        assert result.raw["request_id"] == "req-002"
        assert result.raw["status"] == "completed"

    @pytest.mark.asyncio
    async def test_wait_true_failed_raw_contains_machine_ids_empty(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        async def set_request_id(cmd):
            cmd.created_request_id = "req-003"

        mock_command_bus.execute.side_effect = set_request_id

        poll_result = MagicMock()
        poll_result.status = MagicMock()
        poll_result.status.value = "failed"
        poll_result.machine_references = []
        mock_query_bus.execute.return_value = poll_result

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await orchestrator.execute(
                AcquireMachinesInput(
                    template_id="tmpl-1", requested_count=1, wait=True, timeout_seconds=10
                )
            )

        assert result.raw["machine_ids"] == []
        assert result.raw["status"] == "failed"

    @pytest.mark.asyncio
    async def test_wait_true_timeout_raw_contains_machine_ids_empty(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        async def set_request_id(cmd):
            cmd.created_request_id = "req-004"

        mock_command_bus.execute.side_effect = set_request_id

        poll_result = MagicMock()
        poll_result.status = MagicMock()
        poll_result.status.value = "pending"
        mock_query_bus.execute.return_value = poll_result

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await orchestrator.execute(
                AcquireMachinesInput(
                    template_id="tmpl-1", requested_count=1, wait=True, timeout_seconds=4
                )
            )

        assert result.raw["machine_ids"] == []
        assert result.raw["status"] == "timeout"

    @pytest.mark.asyncio
    async def test_raw_machine_ids_matches_machine_ids(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        async def set_request_id(cmd):
            cmd.created_request_id = "req-005"

        mock_command_bus.execute.side_effect = set_request_id

        m1, m2 = MagicMock(), MagicMock()
        m1.machine_id = "m-111"
        m2.machine_id = "m-222"
        poll_result = MagicMock()
        poll_result.status = MagicMock()
        poll_result.status.value = "completed"
        poll_result.machine_references = [m1, m2]
        mock_query_bus.execute.return_value = poll_result

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await orchestrator.execute(
                AcquireMachinesInput(
                    template_id="tmpl-1", requested_count=2, wait=True, timeout_seconds=10
                )
            )

        assert result.raw["machine_ids"] == result.machine_ids

    @pytest.mark.asyncio
    async def test_poll_consecutive_errors_abort(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        async def set_request_id(cmd):
            cmd.created_request_id = "req-cerr"

        mock_command_bus.execute.side_effect = set_request_id
        mock_query_bus.execute.side_effect = ConnectionError("connection refused")

        input = AcquireMachinesInput(
            template_id="tmpl-1", requested_count=1, wait=True, timeout_seconds=60
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ApplicationError) as exc_info:
                await orchestrator.execute(input)

        assert mock_query_bus.execute.call_count == 3
        assert "req-cerr" in str(exc_info.value)
        assert "consecutive" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_poll_transient_error_then_recovery(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        async def set_request_id(cmd):
            cmd.created_request_id = "req-transient"

        mock_command_bus.execute.side_effect = set_request_id

        terminal = MagicMock()
        terminal.status = MagicMock()
        terminal.status.value = "completed"
        terminal.machine_references = []
        mock_query_bus.execute.side_effect = [ConnectionError("blip"), terminal]

        input = AcquireMachinesInput(
            template_id="tmpl-1", requested_count=1, wait=True, timeout_seconds=60
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await orchestrator.execute(input)

        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_poll_errors_below_threshold_returns_timeout(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        async def set_request_id(cmd):
            cmd.created_request_id = "req-below"

        mock_command_bus.execute.side_effect = set_request_id
        mock_query_bus.execute.side_effect = ConnectionError("connection refused")

        input = AcquireMachinesInput(
            template_id="tmpl-1", requested_count=1, wait=True, timeout_seconds=4
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await orchestrator.execute(input)

        assert result.status == "timeout"
        assert result.machine_ids == []
