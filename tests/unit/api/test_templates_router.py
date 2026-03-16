"""Router-level tests for the templates API endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import get_command_bus, get_query_bus, get_scheduler_strategy
from orb.api.routers.templates import router as templates_router
from orb.application.dto.queries import ListTemplatesQuery, ValidateTemplateQuery
from orb.application.template.commands import (
    CreateTemplateCommand,
    DeleteTemplateCommand,
    UpdateTemplateCommand,
)


@pytest.fixture()
def templates_app():
    app = FastAPI()
    app.include_router(templates_router)
    return app


@pytest.mark.unit
@pytest.mark.api
class TestTemplatesRouter:
    """Tests for the /templates router."""

    def _make_scheduler_mock(self, templates=None):
        from unittest.mock import MagicMock

        scheduler = MagicMock()
        scheduler.format_templates_response.side_effect = lambda t: {
            "templates": [],
            "total_count": len(t),
            "templateCount": len(t),
            "cacheStats": {"refreshed": True},
        }
        return scheduler

    def _make_client(self, app, mock_query_bus=None, mock_command_bus=None, mock_scheduler=None):
        if mock_query_bus is not None:
            app.dependency_overrides[get_query_bus] = lambda: mock_query_bus
        if mock_command_bus is not None:
            app.dependency_overrides[get_command_bus] = lambda: mock_command_bus
        if mock_scheduler is None:
            mock_scheduler = self._make_scheduler_mock()
        app.dependency_overrides[get_scheduler_strategy] = lambda: mock_scheduler
        return TestClient(app, raise_server_exceptions=False)

    def _make_template_dict(self, template_id="tpl-1"):
        return {"template_id": template_id, "name": "Test Template", "provider_api": "aws"}

    # ------------------------------------------------------------------
    # POST /templates/ — create
    # ------------------------------------------------------------------

    def test_create_template_returns_201(self, templates_app):
        command_bus = AsyncMock()
        command_bus.execute = AsyncMock(return_value=None)
        client = self._make_client(templates_app, mock_command_bus=command_bus)

        resp = client.post(
            "/templates/", json={"template_id": "tpl-new", "instance_type": "t3.micro"}
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["template_id"] == "tpl-new"
        command_bus.execute.assert_awaited_once()
        cmd = command_bus.execute.call_args.args[0]
        assert isinstance(cmd, CreateTemplateCommand)
        assert cmd.template_id == "tpl-new"

    def test_create_template_missing_template_id_returns_422(self, templates_app):
        command_bus = AsyncMock()
        client = self._make_client(templates_app, mock_command_bus=command_bus)

        resp = client.post("/templates/", json={"instance_type": "t3.micro"})

        assert resp.status_code == 422

    def test_create_template_dispatches_correct_provider_api(self, templates_app):
        command_bus = AsyncMock()
        command_bus.execute = AsyncMock(return_value=None)
        client = self._make_client(templates_app, mock_command_bus=command_bus)

        client.post("/templates/", json={"template_id": "tpl-x", "provider_api": "aws"})

        cmd = command_bus.execute.call_args.args[0]
        assert cmd.provider_api == "aws"

    # ------------------------------------------------------------------
    # PUT /templates/{id} — update
    # ------------------------------------------------------------------

    def test_update_template_returns_200(self, templates_app):
        command_bus = AsyncMock()
        command_bus.execute = AsyncMock(return_value=None)
        client = self._make_client(templates_app, mock_command_bus=command_bus)

        resp = client.put("/templates/tpl-1", json={"instance_type": "m5.large"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["template_id"] == "tpl-1"
        command_bus.execute.assert_awaited_once()
        cmd = command_bus.execute.call_args.args[0]
        assert isinstance(cmd, UpdateTemplateCommand)
        assert cmd.template_id == "tpl-1"

    def test_update_template_passes_configuration(self, templates_app):
        command_bus = AsyncMock()
        command_bus.execute = AsyncMock(return_value=None)
        client = self._make_client(templates_app, mock_command_bus=command_bus)

        client.put("/templates/tpl-2", json={"name": "Updated Name", "instance_type": "c5.xlarge"})

        cmd = command_bus.execute.call_args.args[0]
        assert cmd.name == "Updated Name"

    # ------------------------------------------------------------------
    # DELETE /templates/{id} — delete
    # ------------------------------------------------------------------

    def test_delete_template_returns_200(self, templates_app):
        command_bus = AsyncMock()
        command_bus.execute = AsyncMock(return_value=None)
        client = self._make_client(templates_app, mock_command_bus=command_bus)

        resp = client.delete("/templates/tpl-del")

        assert resp.status_code == 200
        body = resp.json()
        assert body["template_id"] == "tpl-del"
        command_bus.execute.assert_awaited_once()
        cmd = command_bus.execute.call_args.args[0]
        assert isinstance(cmd, DeleteTemplateCommand)
        assert cmd.template_id == "tpl-del"

    # ------------------------------------------------------------------
    # POST /templates/validate — validate
    # ------------------------------------------------------------------

    def test_validate_template_returns_validation_result(self, templates_app):
        validation_result = MagicMock()
        validation_result.errors = []
        validation_result.warnings = []
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=validation_result)
        client = self._make_client(templates_app, mock_query_bus=query_bus)

        resp = client.post(
            "/templates/validate",
            json={"template_id": "tpl-v", "instance_type": "t3.micro"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["validation_errors"] == []
        query_bus.execute.assert_awaited_once()
        query = query_bus.execute.call_args.args[0]
        assert isinstance(query, ValidateTemplateQuery)

    def test_validate_template_returns_errors_when_invalid(self, templates_app):
        validation_result = MagicMock()
        validation_result.errors = ["missing image_id"]
        validation_result.warnings = []
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=validation_result)
        client = self._make_client(templates_app, mock_query_bus=query_bus)

        resp = client.post("/templates/validate", json={"template_id": "tpl-bad"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert "missing image_id" in body["validation_errors"]

    # ------------------------------------------------------------------
    # POST /templates/refresh — refresh
    # ------------------------------------------------------------------

    def test_refresh_templates_triggers_list_query(self, templates_app):
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=[MagicMock(), MagicMock()])
        client = self._make_client(templates_app, mock_query_bus=query_bus)

        resp = client.post("/templates/refresh")

        assert resp.status_code == 200
        body = resp.json()
        assert body["templateCount"] == 2
        assert body["cacheStats"]["refreshed"] is True
        query_bus.execute.assert_awaited_once()
        query = query_bus.execute.call_args.args[0]
        assert isinstance(query, ListTemplatesQuery)

    def test_refresh_templates_empty_returns_zero_count(self, templates_app):
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=[])
        client = self._make_client(templates_app, mock_query_bus=query_bus)

        resp = client.post("/templates/refresh")

        assert resp.status_code == 200
        assert resp.json()["templateCount"] == 0

    # ------------------------------------------------------------------
    # GET /templates/ — list with provider_api filter
    # ------------------------------------------------------------------

    def test_list_templates_no_filter(self, templates_app):
        tpl = MagicMock()
        tpl.to_dict.return_value = {"template_id": "tpl-1", "provider_api": "aws"}
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=[tpl])
        client = self._make_client(templates_app, mock_query_bus=query_bus)

        resp = client.get("/templates/")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_count"] == 1
        query_bus.execute.assert_awaited_once()
        query = query_bus.execute.call_args.args[0]
        assert isinstance(query, ListTemplatesQuery)
        assert query.provider_api is None

    def test_list_templates_with_provider_api_filter(self, templates_app):
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=[])
        client = self._make_client(templates_app, mock_query_bus=query_bus)

        resp = client.get("/templates/?provider_api=aws")

        assert resp.status_code == 200
        query = query_bus.execute.call_args.args[0]
        assert isinstance(query, ListTemplatesQuery)
        assert query.provider_api == "aws"

    def test_list_templates_with_force_refresh_triggers_list_query(self, templates_app):
        """GET /templates/ with force_refresh=true dispatches ListTemplatesQuery."""
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=[])
        client = self._make_client(templates_app, mock_query_bus=query_bus)

        resp = client.get("/templates/?force_refresh=true")

        # The router always dispatches ListTemplatesQuery; force_refresh is a hint
        assert resp.status_code == 200
        query_bus.execute.assert_awaited_once()
        query = query_bus.execute.call_args.args[0]
        assert isinstance(query, ListTemplatesQuery)

    def test_list_templates_serializes_model_dump(self, templates_app):
        """Templates with model_dump (no to_dict) are serialized correctly."""
        tpl = MagicMock(spec=[])  # no to_dict attribute
        tpl.model_dump = MagicMock(return_value={"template_id": "tpl-md", "provider_api": "aws"})
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=[tpl])
        client = self._make_client(templates_app, mock_query_bus=query_bus)

        resp = client.get("/templates/")

        assert resp.status_code == 200
        assert resp.json()["total_count"] == 1
