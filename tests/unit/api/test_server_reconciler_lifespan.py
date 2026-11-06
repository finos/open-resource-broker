"""The API server starts the request reconciler with the app and stops it on shutdown."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from orb.api import server
from orb.application.services.request_reconciler import RequestReconciler
from orb.config.schemas.server_schema import AuthConfig, ServerConfig


def _fake_reconciler() -> MagicMock:
    reconciler = MagicMock(spec=RequestReconciler)
    reconciler.start.return_value = True
    reconciler.stop = AsyncMock()
    return reconciler


def _app(**server_overrides: object) -> object:
    server_config = ServerConfig(  # type: ignore[call-arg]
        enabled=True,
        auth=AuthConfig(enabled=False, strategy="replace"),  # type: ignore[call-arg]
        **server_overrides,
    )

    def _stub_routes(app: object) -> None:
        app.include_router(APIRouter())  # type: ignore[attr-defined]

    with (
        patch.object(server, "_resolve_event_publisher", return_value=None),
        patch("orb.api.server._register_routers", side_effect=_stub_routes),
    ):
        return server.create_fastapi_app(server_config)


@pytest.mark.unit
def test_reconciler_runs_for_the_life_of_the_app() -> None:
    reconciler = _fake_reconciler()
    with patch.object(server, "_resolve_request_reconciler", return_value=reconciler):
        app = _app()
        with TestClient(app):  # type: ignore[arg-type]
            reconciler.start.assert_called_once()
            reconciler.stop.assert_not_awaited()
        reconciler.stop.assert_awaited_once()


@pytest.mark.unit
def test_reconciler_is_not_started_with_multiple_workers() -> None:
    reconciler = _fake_reconciler()
    with patch.object(server, "_resolve_request_reconciler", return_value=reconciler):
        app = _app(workers=2)
        with TestClient(app):  # type: ignore[arg-type]
            pass
    reconciler.start.assert_not_called()
    reconciler.stop.assert_not_awaited()


@pytest.mark.unit
def test_app_starts_without_a_reconciler() -> None:
    with patch.object(server, "_resolve_request_reconciler", return_value=None):
        app = _app()
        with TestClient(app):  # type: ignore[arg-type]
            pass


@pytest.mark.unit
def test_resolver_ignores_anything_that_is_not_a_reconciler() -> None:
    container = MagicMock()
    container.get_optional.return_value = MagicMock()
    with patch("orb.infrastructure.di.container.get_container", return_value=container):
        assert server._resolve_request_reconciler() is None
