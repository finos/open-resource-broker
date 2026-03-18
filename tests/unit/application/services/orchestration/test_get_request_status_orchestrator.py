"""Unit tests for GetRequestStatusOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import GetRequestQuery, ListActiveRequestsQuery
from orb.application.services.orchestration.dtos import (
    GetRequestStatusInput,
    GetRequestStatusOutput,
)
from orb.application.services.orchestration.get_request_status import GetRequestStatusOrchestrator


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
    return GetRequestStatusOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestGetRequestStatusOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_all_requests_dispatches_list_active_query(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        input = GetRequestStatusInput(all_requests=True)
        await orchestrator.execute(input)
        mock_query_bus.execute.assert_called_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, ListActiveRequestsQuery)

    @pytest.mark.asyncio
    async def test_execute_all_requests_returns_list(self, orchestrator, mock_query_bus):
        r = MagicMock()
        r.model_dump = MagicMock(return_value={"request_id": "req-1"})
        mock_query_bus.execute.return_value = [r]
        input = GetRequestStatusInput(all_requests=True)
        result = await orchestrator.execute(input)
        assert isinstance(result, GetRequestStatusOutput)
        assert len(result.requests) == 1

    @pytest.mark.asyncio
    async def test_execute_specific_ids_dispatches_get_request_query(
        self, orchestrator, mock_query_bus
    ):
        r = MagicMock()
        r.model_dump = MagicMock(return_value={"request_id": "req-1"})
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1"])
        await orchestrator.execute(input)
        mock_query_bus.execute.assert_called_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, GetRequestQuery)
        assert query.request_id == "req-1"

    @pytest.mark.asyncio
    async def test_execute_detailed_sets_long_flag(self, orchestrator, mock_query_bus):
        r = MagicMock()
        r.model_dump = MagicMock(return_value={})
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1"], detailed=True)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert query.long is True
        assert query.lightweight is False

    @pytest.mark.asyncio
    async def test_execute_not_detailed_sets_lightweight_flag(self, orchestrator, mock_query_bus):
        r = MagicMock()
        r.model_dump = MagicMock(return_value={})
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1"], detailed=False)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert query.long is False

    @pytest.mark.asyncio
    async def test_execute_multiple_ids_queries_each(self, orchestrator, mock_query_bus):
        r = MagicMock()
        r.model_dump = MagicMock(return_value={})
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1", "req-2", "req-3"])
        result = await orchestrator.execute(input)
        assert mock_query_bus.execute.call_count == 3
        assert len(result.requests) == 3

    @pytest.mark.asyncio
    async def test_execute_query_error_returns_error_dict(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.side_effect = Exception("not found")
        input = GetRequestStatusInput(request_ids=["req-bad"])
        result = await orchestrator.execute(input)
        assert len(result.requests) == 1
        entry = result.requests[0]
        assert entry["request_id"] == "req-bad"
        assert entry["error"] == "not found"

    @pytest.mark.asyncio
    async def test_execute_query_error_logs_error(self, orchestrator, mock_query_bus, mock_logger):
        mock_query_bus.execute.side_effect = Exception("oops")
        input = GetRequestStatusInput(request_ids=["req-bad"])
        await orchestrator.execute(input)
        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_execute_all_requests_none_result_returns_empty(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.return_value = None
        input = GetRequestStatusInput(all_requests=True)
        result = await orchestrator.execute(input)
        assert result.requests == []

    @pytest.mark.asyncio
    async def test_to_dict_uses_model_dump(self, orchestrator, mock_query_bus):
        r = MagicMock(spec=["model_dump"])
        r.model_dump.return_value = {"key": "value"}
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1"])
        result = await orchestrator.execute(input)
        assert result.requests[0] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_to_dict_falls_back_to_to_dict_method(self, orchestrator, mock_query_bus):
        r = MagicMock(spec=["to_dict"])
        r.to_dict.return_value = {"key": "from_to_dict"}
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1"])
        result = await orchestrator.execute(input)
        assert result.requests[0] == {"key": "from_to_dict"}

    @pytest.mark.asyncio
    async def test_detailed_true_result_contains_expected_keys(self, orchestrator, mock_query_bus):
        """detailed=True: result.requests[0] contains machine_references and status."""
        r = MagicMock(spec=["model_dump"])
        r.model_dump = MagicMock(
            return_value={
                "request_id": "req-detail-1",
                "status": "running",
                "machine_references": ["m-001", "m-002"],
            }
        )
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-detail-1"], detailed=True)

        result = await orchestrator.execute(input)

        assert len(result.requests) == 1
        entry = result.requests[0]
        assert entry["request_id"] == "req-detail-1"
        assert entry["status"] == "running"
        assert entry["machine_references"] == ["m-001", "m-002"]

    @pytest.mark.asyncio
    async def test_execute_query_error_entry_is_dict(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.side_effect = Exception("boom")
        input = GetRequestStatusInput(request_ids=["req-bad"])
        result = await orchestrator.execute(input)
        assert isinstance(result.requests[0], dict)

    @pytest.mark.asyncio
    async def test_execute_query_error_entry_has_request_id_and_error_keys(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.side_effect = Exception("boom")
        input = GetRequestStatusInput(request_ids=["req-bad"])
        result = await orchestrator.execute(input)
        assert result.requests[0].get("request_id") == "req-bad"

    @pytest.mark.asyncio
    async def test_execute_query_error_entry_get_status_returns_empty_string(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.side_effect = Exception("boom")
        input = GetRequestStatusInput(request_ids=["req-bad"])
        result = await orchestrator.execute(input)
        assert result.requests[0].get("status", "") == ""

    @pytest.mark.asyncio
    async def test_execute_mixed_success_and_error_all_entries_are_dicts(
        self, orchestrator, mock_query_bus
    ):
        ok = MagicMock(spec=["model_dump"])
        ok.model_dump = MagicMock(return_value={"request_id": "req-ok", "status": "running"})
        mock_query_bus.execute.side_effect = [ok, Exception("fail")]
        input = GetRequestStatusInput(request_ids=["req-ok", "req-bad"])
        result = await orchestrator.execute(input)
        assert len(result.requests) == 2
        assert all(isinstance(e, dict) for e in result.requests)
