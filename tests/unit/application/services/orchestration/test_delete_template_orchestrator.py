"""Unit tests for DeleteTemplateOrchestrator (issue 2017)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.orchestration.delete_template import DeleteTemplateOrchestrator
from orb.application.services.orchestration.dtos import DeleteTemplateInput, DeleteTemplateOutput
from orb.application.template.commands import DeleteTemplateCommand
from orb.domain.base.exceptions import EntityNotFoundError


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
    return DeleteTemplateOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestDeleteTemplateOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_delete_template_command(self, orchestrator, mock_command_bus):
        async def _set_deleted(cmd):
            cmd.deleted = True

        mock_command_bus.execute.side_effect = _set_deleted
        await orchestrator.execute(DeleteTemplateInput(template_id="t-3"))
        mock_command_bus.execute.assert_awaited_once()
        cmd = mock_command_bus.execute.call_args[0][0]
        assert isinstance(cmd, DeleteTemplateCommand)
        assert cmd.template_id == "t-3"

    @pytest.mark.asyncio
    async def test_execute_returns_deleted_true(self, orchestrator, mock_command_bus):
        async def _set_deleted(cmd):
            cmd.deleted = True

        mock_command_bus.execute.side_effect = _set_deleted
        result = await orchestrator.execute(DeleteTemplateInput(template_id="t-3"))
        assert isinstance(result, DeleteTemplateOutput)
        assert result.deleted is True
        assert result.template_id == "t-3"

    @pytest.mark.asyncio
    async def test_execute_returns_deleted_false(self, orchestrator, mock_command_bus):
        async def _set_deleted(cmd):
            cmd.deleted = False

        mock_command_bus.execute.side_effect = _set_deleted
        result = await orchestrator.execute(DeleteTemplateInput(template_id="t-3"))
        assert result.deleted is False

    @pytest.mark.asyncio
    async def test_execute_raw_contains_expected_keys(self, orchestrator, mock_command_bus):
        async def _set_deleted(cmd):
            cmd.deleted = True

        mock_command_bus.execute.side_effect = _set_deleted
        result = await orchestrator.execute(DeleteTemplateInput(template_id="t-3"))
        assert set(result.raw.keys()) >= {"template_id", "status", "deleted"}
        assert result.raw["status"] == "deleted"

    # --- 2017: EntityNotFoundError handling ---

    @pytest.mark.asyncio
    async def test_entity_not_found_returns_deleted_false(self, orchestrator, mock_command_bus):
        mock_command_bus.execute.side_effect = EntityNotFoundError("Template", "tmpl-1")
        result = await orchestrator.execute(DeleteTemplateInput(template_id="tmpl-1"))
        assert result.deleted is False
        assert result.template_id == "tmpl-1"

    @pytest.mark.asyncio
    async def test_entity_not_found_returns_not_found_status(self, orchestrator, mock_command_bus):
        mock_command_bus.execute.side_effect = EntityNotFoundError("Template", "tmpl-1")
        result = await orchestrator.execute(DeleteTemplateInput(template_id="tmpl-1"))
        assert result.raw["status"] == "not_found"
        assert result.raw["deleted"] is False

    @pytest.mark.asyncio
    async def test_entity_not_found_does_not_propagate(self, orchestrator, mock_command_bus):
        mock_command_bus.execute.side_effect = EntityNotFoundError("Template", "tmpl-1")
        # Should not raise
        result = await orchestrator.execute(DeleteTemplateInput(template_id="tmpl-1"))
        assert result is not None

    @pytest.mark.asyncio
    async def test_other_exceptions_still_propagate(self, orchestrator, mock_command_bus):
        mock_command_bus.execute.side_effect = ValueError("unexpected")
        with pytest.raises(ValueError, match="unexpected"):
            await orchestrator.execute(DeleteTemplateInput(template_id="tmpl-x"))

    @pytest.mark.asyncio
    async def test_consistency_with_get_template_orchestrator(self, orchestrator, mock_command_bus):
        """Both delete and get return a valid output DTO (not None, not raise) on not-found."""
        from orb.application.services.orchestration.dtos import GetTemplateInput
        from orb.application.services.orchestration.get_template import GetTemplateOrchestrator

        mock_qbus = MagicMock()
        mock_qbus.execute = AsyncMock(side_effect=EntityNotFoundError("Template", "t-x"))
        get_orch = GetTemplateOrchestrator(
            command_bus=MagicMock(), query_bus=mock_qbus, logger=MagicMock()
        )

        mock_command_bus.execute.side_effect = EntityNotFoundError("Template", "t-x")
        delete_result = await orchestrator.execute(DeleteTemplateInput(template_id="t-x"))
        get_result = await get_orch.execute(GetTemplateInput(template_id="t-x"))

        assert delete_result is not None
        assert get_result is not None
        assert isinstance(delete_result, DeleteTemplateOutput)
