"""Unit tests for the system router — /system/dashboard and _serialisable helper."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import get_current_user, get_dashboard_summary_orchestrator
from orb.api.routers.system import _serialisable, router as system_router
from orb.application.services.orchestration.dtos import DashboardSummaryInput

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(*, role: str = "viewer") -> FastAPI:
    from fastapi.responses import JSONResponse

    from orb.api.dependencies import CurrentUser
    from orb.infrastructure.error.exception_handler import get_exception_handler

    app = FastAPI()
    app.include_router(system_router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="test-user", role=role
    )
    exception_handler = get_exception_handler()

    @app.exception_handler(Exception)
    async def global_exception_handler(__request, exc):
        from fastapi import HTTPException

        if isinstance(exc, HTTPException):
            raise exc
        error_response = exception_handler.handle_error_for_http(exc)
        return JSONResponse(
            status_code=error_response.http_status or 500,
            content={"detail": error_response.message},
        )

    return app


def _make_dashboard_output() -> dict[str, Any]:
    return {
        "machines": {"total": 5, "by_status": {"running": 5}},
        "requests": {"total": 2, "in_flight": 1, "by_status": {}},
        "templates": {"total": 3, "by_provider_api": {}},
        "recent_activity": [],
    }


# ---------------------------------------------------------------------------
# Auth guard tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestSystemAuthGuard:
    def test_unknown_role_returns_403(self):
        from orb.api.dependencies import CurrentUser

        app = _make_app()
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="nobody", role="no_such_role"
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/system/dashboard")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Dashboard endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestDashboardEndpoint:
    def _make_orchestrator(self, output: Any = None):
        if output is None:
            output = _make_dashboard_output()
        orc = AsyncMock()
        orc.execute = AsyncMock(return_value=output)
        return orc

    def test_returns_200_on_happy_path(self):
        app = _make_app()
        app.dependency_overrides[get_dashboard_summary_orchestrator] = lambda: (
            self._make_orchestrator()
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/system/dashboard")
        assert resp.status_code == 200

    def test_response_contains_expected_keys(self):
        app = _make_app()
        app.dependency_overrides[get_dashboard_summary_orchestrator] = lambda: (
            self._make_orchestrator()
        )
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/system/dashboard").json()
        for key in ("machines", "requests", "templates"):
            assert key in body, f"expected key '{key}' in response"

    def test_orchestrator_receives_dashboard_summary_input(self):
        """The endpoint passes a DashboardSummaryInput instance to orchestrator."""
        captured: list[Any] = []
        output = _make_dashboard_output()

        async def _execute(inp):
            captured.append(inp)
            return output

        orc = AsyncMock()
        orc.execute = _execute
        app = _make_app()
        app.dependency_overrides[get_dashboard_summary_orchestrator] = lambda: orc
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/system/dashboard")
        assert len(captured) == 1
        assert isinstance(captured[0], DashboardSummaryInput)

    def test_machines_total_correct(self):
        app = _make_app()
        app.dependency_overrides[get_dashboard_summary_orchestrator] = lambda: (
            self._make_orchestrator()
        )
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/system/dashboard").json()
        assert body["machines"]["total"] == 5

    def test_viewer_can_access_dashboard(self):
        """Viewer role is sufficient to access dashboard."""
        app = _make_app(role="viewer")
        app.dependency_overrides[get_dashboard_summary_orchestrator] = lambda: (
            self._make_orchestrator()
        )
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/system/dashboard").status_code == 200


# ---------------------------------------------------------------------------
# _serialisable recursion tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSerialisable:
    def test_plain_int_passes_through(self):
        assert _serialisable(42) == 42

    def test_plain_string_passes_through(self):
        assert _serialisable("hello") == "hello"

    def test_none_passes_through(self):
        assert _serialisable(None) is None

    def test_dict_values_are_recursed(self):
        @dataclasses.dataclass
        class Inner:
            x: int

        result = _serialisable({"a": Inner(x=7)})
        assert result == {"a": {"x": 7}}

    def test_list_items_are_recursed(self):
        @dataclasses.dataclass
        class Node:
            value: str

        result = _serialisable([Node(value="foo"), Node(value="bar")])
        assert result == [{"value": "foo"}, {"value": "bar"}]

    def test_nested_list_in_dict(self):
        @dataclasses.dataclass
        class Item:
            n: int

        result = _serialisable({"items": [Item(n=1), Item(n=2)]})
        assert result == {"items": [{"n": 1}, {"n": 2}]}

    def test_dataclass_fields_are_serialised(self):
        @dataclasses.dataclass
        class Summary:
            total: int
            label: str

        result = _serialisable(Summary(total=10, label="test"))
        assert result == {"total": 10, "label": "test"}

    def test_datetime_passes_through_unchanged(self):
        """_serialisable does not convert datetimes — it passes them through.

        The JSON serialisation of datetimes is handled downstream by FastAPI /
        JSONResponse.  This test verifies that _serialisable does not crash on
        datetime values and returns them unchanged.
        """
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = _serialisable({"ts": dt})
        assert result["ts"] is dt

    def test_nested_dataclass_is_flattened(self):
        @dataclasses.dataclass
        class Outer:
            name: str
            count: int

        @dataclasses.dataclass
        class Inner:
            outer: Outer

        result = _serialisable(Inner(outer=Outer(name="x", count=3)))
        assert result == {"outer": {"name": "x", "count": 3}}

    def test_tuple_is_returned_as_list(self):
        result = _serialisable((1, 2, 3))
        assert result == [1, 2, 3]
