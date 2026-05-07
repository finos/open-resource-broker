"""Unit tests for GetRequestStatusOrchestrator."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.orchestration.dtos import (
    GetRequestStatusInput,
    GetRequestStatusOutput,
)
from orb.application.services.orchestration.get_request_status import (
    GetRequestStatusOrchestrator,
)


def _make_orchestrator():
    command_bus = MagicMock()
    query_bus = MagicMock()
    query_bus.execute = AsyncMock()
    logger = MagicMock()
    return GetRequestStatusOrchestrator(command_bus, query_bus, logger), query_bus


@pytest.mark.unit
class TestGetRequestStatusOrchestratorVerbose:
    """verbose lives on GetRequestStatusInput, not on RequestDTO."""

    def test_verbose_field_exists_on_input_dto(self):
        inp = GetRequestStatusInput(request_ids=["req-1"], verbose=True)
        assert inp.verbose is True

    def test_verbose_defaults_to_false(self):
        inp = GetRequestStatusInput(request_ids=["req-1"])
        assert inp.verbose is False

    @pytest.mark.asyncio
    async def test_orchestrator_passes_verbose_to_query(self):
        orchestrator, query_bus = _make_orchestrator()
        query_bus.execute.return_value = MagicMock(to_dict=lambda: {"request_id": "req-1"})

        await orchestrator.execute(GetRequestStatusInput(request_ids=["req-1"], verbose=True))

        call_args = query_bus.execute.call_args[0][0]
        assert call_args.verbose is True

    @pytest.mark.asyncio
    async def test_orchestrator_returns_output_type(self):
        orchestrator, query_bus = _make_orchestrator()
        query_bus.execute.return_value = MagicMock(to_dict=lambda: {"request_id": "req-1"})

        result = await orchestrator.execute(GetRequestStatusInput(request_ids=["req-1"]))

        assert isinstance(result, GetRequestStatusOutput)
        assert len(result.requests) == 1

    @pytest.mark.asyncio
    async def test_orchestrator_handles_query_error_gracefully(self):
        orchestrator, query_bus = _make_orchestrator()
        query_bus.execute.side_effect = Exception("not found")

        result = await orchestrator.execute(GetRequestStatusInput(request_ids=["req-bad"]))

        assert len(result.requests) == 1
        assert result.requests[0]["request_id"] == "req-bad"
        assert "error" in result.requests[0]
