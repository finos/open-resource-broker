"""Unit tests for the 6 explicit orchestrator-backed template methods on ORBClient."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.sdk.client import ORBClient
from orb.sdk.exceptions import NotFoundError, SDKError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _initialized_sdk() -> ORBClient:
    sdk = ORBClient(config={"provider": "aws"})
    sdk._initialized = True
    sdk._container = MagicMock()
    return sdk


def _mock_container(sdk: ORBClient, orchestrator_class, orchestrator, scheduler=None):
    """Wire container.get() for one orchestrator and container.get_optional() for scheduler."""

    def _get(cls):
        if cls is orchestrator_class:
            return orchestrator
        raise KeyError(cls)

    sdk._container.get.side_effect = _get
    sdk._container.get_optional.return_value = scheduler


# ---------------------------------------------------------------------------
# get_template
# ---------------------------------------------------------------------------


class TestGetTemplate:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_happy_path_with_scheduler(self):
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import GetTemplateOutput
        from orb.application.services.orchestration.get_template import GetTemplateOrchestrator

        mock_template = MagicMock()
        mock_template.to_dict.return_value = {"template_id": "t1", "name": "my-template"}

        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(return_value=GetTemplateOutput(template=mock_template))

        mock_scheduler = MagicMock(spec=SchedulerPort)
        mock_scheduler.format_template_for_display.return_value = {
            "template_id": "t1",
            "name": "my-template",
        }

        sdk = _initialized_sdk()
        _mock_container(sdk, GetTemplateOrchestrator, mock_orch, mock_scheduler)

        result = await sdk.get_template("t1")

        mock_orch.execute.assert_awaited_once()
        mock_scheduler.format_template_for_display.assert_called_once_with(mock_template)
        assert result == {"template_id": "t1", "name": "my-template"}

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_raises_not_found_when_template_is_none(self):
        from orb.application.services.orchestration.dtos import GetTemplateOutput
        from orb.application.services.orchestration.get_template import GetTemplateOrchestrator

        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(return_value=GetTemplateOutput(template=None))

        sdk = _initialized_sdk()
        _mock_container(sdk, GetTemplateOrchestrator, mock_orch)

        with pytest.raises(NotFoundError) as exc_info:
            await sdk.get_template("missing")

        assert exc_info.value.entity_type == "Template"
        assert exc_info.value.entity_id == "missing"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_raises_sdk_error_when_not_initialized(self):
        sdk = ORBClient(config={"provider": "aws"})
        with pytest.raises(SDKError):
            await sdk.get_template("t1")


# ---------------------------------------------------------------------------
# create_template
# ---------------------------------------------------------------------------


class TestCreateTemplate:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_happy_path_with_scheduler(self):
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.create_template import (
            CreateTemplateOrchestrator,
        )
        from orb.application.services.orchestration.dtos import CreateTemplateOutput

        raw = {"template_id": "t1", "status": "created", "validation_errors": []}
        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(
            return_value=CreateTemplateOutput(template_id="t1", created=True, raw=raw)
        )

        mock_scheduler = MagicMock(spec=SchedulerPort)
        mock_scheduler.format_template_mutation_response.return_value = raw

        sdk = _initialized_sdk()
        _mock_container(sdk, CreateTemplateOrchestrator, mock_orch, mock_scheduler)

        result = await sdk.create_template("t1", "EC2Fleet", "ami-123")

        mock_orch.execute.assert_awaited_once()
        mock_scheduler.format_template_mutation_response.assert_called_once_with(raw)
        assert result == raw

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_raises_sdk_error_when_not_initialized(self):
        sdk = ORBClient(config={"provider": "aws"})
        with pytest.raises(SDKError):
            await sdk.create_template("t1", "EC2Fleet", "ami-123")


# ---------------------------------------------------------------------------
# update_template
# ---------------------------------------------------------------------------


class TestUpdateTemplate:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_happy_path_with_scheduler(self):
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import UpdateTemplateOutput
        from orb.application.services.orchestration.update_template import (
            UpdateTemplateOrchestrator,
        )

        raw = {"template_id": "t1", "status": "updated", "validation_errors": []}
        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(
            return_value=UpdateTemplateOutput(template_id="t1", updated=True, raw=raw)
        )

        mock_scheduler = MagicMock(spec=SchedulerPort)
        mock_scheduler.format_template_mutation_response.return_value = raw

        sdk = _initialized_sdk()
        _mock_container(sdk, UpdateTemplateOrchestrator, mock_orch, mock_scheduler)

        result = await sdk.update_template("t1", name="new-name")

        mock_orch.execute.assert_awaited_once()
        mock_scheduler.format_template_mutation_response.assert_called_once_with(raw)
        assert result == raw

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_raises_sdk_error_when_not_initialized(self):
        sdk = ORBClient(config={"provider": "aws"})
        with pytest.raises(SDKError):
            await sdk.update_template("t1")


# ---------------------------------------------------------------------------
# delete_template
# ---------------------------------------------------------------------------


class TestDeleteTemplate:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_happy_path_with_scheduler(self):
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.delete_template import (
            DeleteTemplateOrchestrator,
        )
        from orb.application.services.orchestration.dtos import DeleteTemplateOutput

        raw = {"template_id": "t1", "status": "deleted", "validation_errors": []}
        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(
            return_value=DeleteTemplateOutput(template_id="t1", deleted=True, raw=raw)
        )

        mock_scheduler = MagicMock(spec=SchedulerPort)
        mock_scheduler.format_template_mutation_response.return_value = raw

        sdk = _initialized_sdk()
        _mock_container(sdk, DeleteTemplateOrchestrator, mock_orch, mock_scheduler)

        result = await sdk.delete_template("t1")

        mock_orch.execute.assert_awaited_once()
        mock_scheduler.format_template_mutation_response.assert_called_once_with(raw)
        assert result == raw

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_raises_sdk_error_when_not_initialized(self):
        sdk = ORBClient(config={"provider": "aws"})
        with pytest.raises(SDKError):
            await sdk.delete_template("t1")


# ---------------------------------------------------------------------------
# validate_template
# ---------------------------------------------------------------------------


class TestValidateTemplate:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_happy_path_with_scheduler(self):
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import ValidateTemplateOutput
        from orb.application.services.orchestration.validate_template import (
            ValidateTemplateOrchestrator,
        )

        raw = {"template_id": "t1", "status": "validated", "valid": True, "validation_errors": []}
        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(
            return_value=ValidateTemplateOutput(valid=True, errors=[], raw=raw)
        )

        mock_scheduler = MagicMock(spec=SchedulerPort)
        mock_scheduler.format_template_mutation_response.return_value = raw

        sdk = _initialized_sdk()
        _mock_container(sdk, ValidateTemplateOrchestrator, mock_orch, mock_scheduler)

        result = await sdk.validate_template(template_id="t1")

        mock_orch.execute.assert_awaited_once()
        mock_scheduler.format_template_mutation_response.assert_called_once_with(raw)
        assert result == raw

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_raises_sdk_error_when_not_initialized(self):
        sdk = ORBClient(config={"provider": "aws"})
        with pytest.raises(SDKError):
            await sdk.validate_template(template_id="t1")


# ---------------------------------------------------------------------------
# refresh_templates
# ---------------------------------------------------------------------------


class TestRefreshTemplates:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_happy_path_with_scheduler(self):
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import RefreshTemplatesOutput
        from orb.application.services.orchestration.refresh_templates import (
            RefreshTemplatesOrchestrator,
        )

        mock_t1 = {"template_id": "t1"}
        mock_t2 = {"template_id": "t2"}
        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(
            return_value=RefreshTemplatesOutput(templates=[mock_t1, mock_t2])
        )

        mock_scheduler = MagicMock(spec=SchedulerPort)
        mock_scheduler.format_templates_response.return_value = {"templates": [mock_t1, mock_t2]}

        sdk = _initialized_sdk()
        _mock_container(sdk, RefreshTemplatesOrchestrator, mock_orch, mock_scheduler)

        result = await sdk.refresh_templates()

        mock_orch.execute.assert_awaited_once()
        mock_scheduler.format_templates_response.assert_called_once_with([mock_t1, mock_t2])
        assert result == {"templates": [mock_t1, mock_t2]}

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_raises_sdk_error_when_not_initialized(self):
        sdk = ORBClient(config={"provider": "aws"})
        with pytest.raises(SDKError):
            await sdk.refresh_templates()
