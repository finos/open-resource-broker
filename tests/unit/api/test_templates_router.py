"""Router-level tests for the templates API endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import (
    get_create_template_orchestrator,
    get_delete_template_orchestrator,
    get_get_template_orchestrator,
    get_list_templates_orchestrator,
    get_refresh_templates_orchestrator,
    get_scheduler_strategy,
    get_update_template_orchestrator,
    get_validate_template_orchestrator,
)
from orb.api.routers.templates import router as templates_router
from orb.application.services.orchestration.dtos import (
    CreateTemplateOutput,
    DeleteTemplateOutput,
    GetTemplateOutput,
    ListTemplatesOutput,
    RefreshTemplatesOutput,
    UpdateTemplateOutput,
    ValidateTemplateOutput,
)
from orb.domain.base.exceptions import DuplicateError, EntityNotFoundError


@pytest.fixture()
def templates_app():
    from fastapi.responses import JSONResponse

    from orb.infrastructure.error.exception_handler import get_exception_handler

    app = FastAPI()
    app.include_router(templates_router)

    exception_handler = get_exception_handler()

    @app.exception_handler(Exception)
    async def global_exception_handler(__request, exc):  # noqa: N807
        error_response = exception_handler.handle_error_for_http(exc)
        return JSONResponse(
            status_code=error_response.http_status or 500,
            content={"detail": error_response.message},
        )

    return app


@pytest.mark.unit
@pytest.mark.api
class TestTemplatesRouter:
    """Tests for the /templates router."""

    def _make_scheduler_mock(self):
        scheduler = MagicMock()
        scheduler.format_templates_response.side_effect = lambda t: {
            "templates": [],
            "total_count": len(t),
            "templateCount": len(t),
            "cacheStats": {"refreshed": True},
        }
        scheduler.format_template_for_display.side_effect = lambda t: t
        scheduler.format_template_mutation_response.side_effect = lambda raw: {
            "template_id": raw.get("template_id"),
            "status": raw.get("status"),
            "validation_errors": raw.get("validation_errors", []),
            "valid": raw.get("valid"),
        }
        return scheduler

    def _make_client(self, app, overrides=None):
        scheduler = self._make_scheduler_mock()
        app.dependency_overrides[get_scheduler_strategy] = lambda: scheduler
        for dep, factory in (overrides or {}).items():
            app.dependency_overrides[dep] = factory
        return TestClient(app, raise_server_exceptions=False)

    # ------------------------------------------------------------------
    # POST /templates/ — create
    # ------------------------------------------------------------------

    def test_create_template_returns_201(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=CreateTemplateOutput(
                template_id="tpl-new",
                created=True,
                raw={"template_id": "tpl-new", "status": "created", "validation_errors": []},
            )
        )
        client = self._make_client(
            templates_app, {get_create_template_orchestrator: lambda: orchestrator}
        )

        resp = client.post(
            "/templates/", json={"template_id": "tpl-new", "instance_type": "t3.micro"}
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["template_id"] == "tpl-new"
        assert body["status"] == "created"
        orchestrator.execute.assert_awaited_once()

    def test_create_template_missing_template_id_returns_422(self, templates_app):
        orchestrator = AsyncMock()
        client = self._make_client(
            templates_app, {get_create_template_orchestrator: lambda: orchestrator}
        )

        resp = client.post("/templates/", json={"instance_type": "t3.micro"})

        assert resp.status_code == 422

    def test_create_template_duplicate_returns_409(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(side_effect=DuplicateError("already exists"))
        client = self._make_client(
            templates_app, {get_create_template_orchestrator: lambda: orchestrator}
        )

        resp = client.post("/templates/", json={"template_id": "tpl-dup"})

        assert resp.status_code == 409

    def test_create_template_validation_error_returns_400(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(side_effect=ValueError("bad value"))
        client = self._make_client(
            templates_app, {get_create_template_orchestrator: lambda: orchestrator}
        )

        resp = client.post("/templates/", json={"template_id": "tpl-bad"})

        assert resp.status_code == 400

    def test_create_template_passes_description_to_orchestrator(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=CreateTemplateOutput(
                template_id="tpl-1",
                created=True,
                raw={"template_id": "tpl-1", "status": "created", "validation_errors": []},
            )
        )
        client = self._make_client(
            templates_app, {get_create_template_orchestrator: lambda: orchestrator}
        )

        client.post("/templates/", json={"template_id": "tpl-1", "description": "my desc"})

        orchestrator.execute.assert_awaited_once()
        inp = orchestrator.execute.call_args.args[0]
        assert inp.description == "my desc"

    def test_create_template_returns_validation_errors_in_body(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=CreateTemplateOutput(
                template_id="tpl-warn",
                created=True,
                validation_errors=["missing image_id"],
                raw={
                    "template_id": "tpl-warn",
                    "status": "created",
                    "validation_errors": ["missing image_id"],
                },
            )
        )
        client = self._make_client(
            templates_app, {get_create_template_orchestrator: lambda: orchestrator}
        )

        resp = client.post("/templates/", json={"template_id": "tpl-warn"})

        assert resp.status_code == 201
        assert resp.json()["validation_errors"] == ["missing image_id"]

    # ------------------------------------------------------------------
    # PUT /templates/{id} — update
    # ------------------------------------------------------------------

    def test_update_template_returns_200(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=UpdateTemplateOutput(
                template_id="tpl-1",
                updated=True,
                raw={"template_id": "tpl-1", "status": "updated", "validation_errors": []},
            )
        )
        client = self._make_client(
            templates_app, {get_update_template_orchestrator: lambda: orchestrator}
        )

        resp = client.put("/templates/tpl-1", json={"instance_type": "m5.large"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["template_id"] == "tpl-1"
        assert body["status"] == "updated"
        orchestrator.execute.assert_awaited_once()

    def test_update_template_passes_description_to_orchestrator(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=UpdateTemplateOutput(
                template_id="tpl-1",
                updated=True,
                raw={"template_id": "tpl-1", "status": "updated", "validation_errors": []},
            )
        )
        client = self._make_client(
            templates_app, {get_update_template_orchestrator: lambda: orchestrator}
        )

        client.put("/templates/tpl-1", json={"description": "updated desc"})

        orchestrator.execute.assert_awaited_once()
        inp = orchestrator.execute.call_args.args[0]
        assert inp.description == "updated desc"

    def test_update_template_not_found_returns_404(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(side_effect=EntityNotFoundError("Template", "tpl-missing"))
        client = self._make_client(
            templates_app, {get_update_template_orchestrator: lambda: orchestrator}
        )

        resp = client.put("/templates/tpl-missing", json={"name": "x"})

        assert resp.status_code == 404

    def test_update_template_returns_validation_errors_in_body(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=UpdateTemplateOutput(
                template_id="tpl-1",
                updated=True,
                validation_errors=["bad field"],
                raw={
                    "template_id": "tpl-1",
                    "status": "updated",
                    "validation_errors": ["bad field"],
                },
            )
        )
        client = self._make_client(
            templates_app, {get_update_template_orchestrator: lambda: orchestrator}
        )

        resp = client.put("/templates/tpl-1", json={"name": "x"})

        assert resp.status_code == 200
        assert resp.json()["validation_errors"] == ["bad field"]

    # ------------------------------------------------------------------
    # DELETE /templates/{id} — delete
    # ------------------------------------------------------------------

    def test_delete_template_returns_200(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=DeleteTemplateOutput(
                template_id="tpl-del",
                deleted=True,
                raw={"template_id": "tpl-del", "status": "deleted", "validation_errors": []},
            )
        )
        client = self._make_client(
            templates_app, {get_delete_template_orchestrator: lambda: orchestrator}
        )

        resp = client.delete("/templates/tpl-del")

        assert resp.status_code == 200
        body = resp.json()
        assert body["template_id"] == "tpl-del"
        assert body["status"] == "deleted"
        orchestrator.execute.assert_awaited_once()

    def test_delete_template_not_found_returns_404(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(side_effect=EntityNotFoundError("Template", "tpl-gone"))
        client = self._make_client(
            templates_app, {get_delete_template_orchestrator: lambda: orchestrator}
        )

        resp = client.delete("/templates/tpl-gone")

        assert resp.status_code == 404

    # ------------------------------------------------------------------
    # GET /templates/{id} — get single
    # ------------------------------------------------------------------

    def test_get_template_returns_200(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=GetTemplateOutput(template={"template_id": "tpl-1"})
        )
        client = self._make_client(
            templates_app, {get_get_template_orchestrator: lambda: orchestrator}
        )

        resp = client.get("/templates/tpl-1")

        assert resp.status_code == 200
        orchestrator.execute.assert_awaited_once()

    def test_get_template_not_found_returns_404(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=GetTemplateOutput(template={}))
        client = self._make_client(
            templates_app, {get_get_template_orchestrator: lambda: orchestrator}
        )

        resp = client.get("/templates/tpl-missing")

        assert resp.status_code == 404

    # ------------------------------------------------------------------
    # POST /templates/validate — validate
    # ------------------------------------------------------------------

    def test_validate_template_returns_validation_result(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=ValidateTemplateOutput(
                valid=True,
                errors=[],
                raw={
                    "template_id": "tpl-v",
                    "status": "validated",
                    "valid": True,
                    "validation_errors": [],
                },
            )
        )
        client = self._make_client(
            templates_app, {get_validate_template_orchestrator: lambda: orchestrator}
        )

        resp = client.post(
            "/templates/validate",
            json={"template_id": "tpl-v", "instance_type": "t3.micro"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["validation_errors"] == []
        orchestrator.execute.assert_awaited_once()

    def test_validate_template_returns_errors_when_invalid(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=ValidateTemplateOutput(
                valid=False,
                errors=["missing image_id"],
                raw={
                    "template_id": "tpl-bad",
                    "status": "validated",
                    "valid": False,
                    "validation_errors": ["missing image_id"],
                },
            )
        )
        client = self._make_client(
            templates_app, {get_validate_template_orchestrator: lambda: orchestrator}
        )

        resp = client.post("/templates/validate", json={"template_id": "tpl-bad"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert "missing image_id" in body["validation_errors"]

    # ------------------------------------------------------------------
    # POST /templates/refresh — refresh
    # ------------------------------------------------------------------

    def test_refresh_templates_returns_200(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=RefreshTemplatesOutput(templates=[{"id": "a"}, {"id": "b"}])
        )
        client = self._make_client(
            templates_app, {get_refresh_templates_orchestrator: lambda: orchestrator}
        )

        resp = client.post("/templates/refresh")

        assert resp.status_code == 200
        body = resp.json()
        assert body["templateCount"] == 2
        assert body["cacheStats"]["refreshed"] is True
        orchestrator.execute.assert_awaited_once()

    def test_refresh_templates_empty_returns_zero_count(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=RefreshTemplatesOutput(templates=[]))
        client = self._make_client(
            templates_app, {get_refresh_templates_orchestrator: lambda: orchestrator}
        )

        resp = client.post("/templates/refresh")

        assert resp.status_code == 200
        assert resp.json()["templateCount"] == 0

    # ------------------------------------------------------------------
    # GET /templates/ — list
    # ------------------------------------------------------------------

    def test_list_templates_no_filter(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=ListTemplatesOutput(templates=[MagicMock()]))
        client = self._make_client(
            templates_app, {get_list_templates_orchestrator: lambda: orchestrator}
        )

        resp = client.get("/templates/")

        assert resp.status_code == 200
        assert resp.json()["total_count"] == 1
        orchestrator.execute.assert_awaited_once()
        inp = orchestrator.execute.call_args.args[0]
        assert inp.provider_api is None

    def test_list_templates_with_provider_api_filter(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=ListTemplatesOutput(templates=[]))
        client = self._make_client(
            templates_app, {get_list_templates_orchestrator: lambda: orchestrator}
        )

        resp = client.get("/templates/?provider_api=aws")

        assert resp.status_code == 200
        inp = orchestrator.execute.call_args.args[0]
        assert inp.provider_api == "aws"

    def test_list_templates_serializes_correctly(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=ListTemplatesOutput(templates=[MagicMock()]))
        client = self._make_client(
            templates_app, {get_list_templates_orchestrator: lambda: orchestrator}
        )

        resp = client.get("/templates/")

        assert resp.status_code == 200
        assert resp.json()["total_count"] == 1


@pytest.mark.unit
@pytest.mark.api
class TestTemplatesRouteOrder:
    """2025: /validate and /refresh must be registered before /{template_id}."""

    def _make_scheduler_mock(self):
        scheduler = MagicMock()
        scheduler.format_templates_response.side_effect = lambda t: {
            "templates": [],
            "total_count": len(t),
            "templateCount": len(t),
            "cacheStats": {"refreshed": True},
        }
        scheduler.format_template_for_display.side_effect = lambda t: t
        scheduler.format_template_mutation_response.side_effect = lambda raw: {
            "template_id": raw.get("template_id"),
            "status": raw.get("status"),
            "validation_errors": raw.get("validation_errors", []),
            "valid": raw.get("valid"),
        }
        return scheduler

    def _make_client(self, app, overrides=None):
        from orb.api.dependencies import get_scheduler_strategy

        scheduler = self._make_scheduler_mock()
        app.dependency_overrides[get_scheduler_strategy] = lambda: scheduler
        for dep, factory in (overrides or {}).items():
            app.dependency_overrides[dep] = factory
        return TestClient(app, raise_server_exceptions=False)

    def test_route_registration_order_validate_before_template_id(self):
        """Index of /validate route must be less than index of /{template_id}."""
        from fastapi.routing import APIRoute

        from orb.api.routers.templates import router

        paths = [r.path for r in router.routes if isinstance(r, APIRoute)]
        assert "/templates/validate" in paths, "Missing /validate route"
        assert "/templates/{template_id}" in paths, "Missing /{template_id} route"
        assert paths.index("/templates/validate") < paths.index("/templates/{template_id}")

    def test_route_registration_order_refresh_before_template_id(self):
        """Index of /refresh route must be less than index of /{template_id}."""
        from fastapi.routing import APIRoute

        from orb.api.routers.templates import router

        paths = [r.path for r in router.routes if isinstance(r, APIRoute)]
        assert "/templates/refresh" in paths, "Missing /refresh route"
        assert paths.index("/templates/refresh") < paths.index("/templates/{template_id}")

    def test_get_validate_not_shadowed_by_template_id(self, templates_app):
        """GET /templates/validate is handled by /{template_id} (not shadowed/404 from routing)."""
        client = self._make_client(templates_app)
        resp = client.get("/templates/validate")
        # /{template_id} GET route matches; orchestrator returns 404 for unknown template
        assert resp.status_code != 405, "Should not be a method-not-allowed routing error"

    def test_get_refresh_not_shadowed_by_template_id(self, templates_app):
        """GET /templates/refresh is handled by /{template_id} (not shadowed/404 from routing)."""
        client = self._make_client(templates_app)
        resp = client.get("/templates/refresh")
        # /{template_id} GET route matches; orchestrator returns 404 for unknown template
        assert resp.status_code != 405, "Should not be a method-not-allowed routing error"

    def test_post_validate_is_reachable(self, templates_app):
        """POST /templates/validate must not return 404."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=ValidateTemplateOutput(
                valid=True,
                errors=[],
                raw={
                    "template_id": None,
                    "status": "validated",
                    "valid": True,
                    "validation_errors": [],
                },
            )
        )
        client = self._make_client(
            templates_app, {get_validate_template_orchestrator: lambda: orchestrator}
        )
        resp = client.post("/templates/validate", json={"template_id": "t1"})
        assert resp.status_code != 404

    def test_post_refresh_is_reachable(self, templates_app):
        """POST /templates/refresh must not return 404."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=RefreshTemplatesOutput(templates=[]))
        client = self._make_client(
            templates_app, {get_refresh_templates_orchestrator: lambda: orchestrator}
        )
        resp = client.post("/templates/refresh")
        assert resp.status_code != 404

    def test_get_template_by_id_still_works(self, templates_app):
        """Regression: GET /templates/some-id still routes to get_template handler."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=GetTemplateOutput(template={"template_id": "some-id"})
        )
        client = self._make_client(
            templates_app, {get_get_template_orchestrator: lambda: orchestrator}
        )
        resp = client.get("/templates/some-id")
        assert resp.status_code in (200, 404)
        assert resp.status_code != 405
