"""Unit tests for api/routers/providers.py covering previously uncovered branches.

Targets (providers.py):
  - _probe_provider_health(): exception path → "unknown", unhealthy result → "degraded",
    healthy result with response_time_ms and status_message (lines 34-72)
  - _get_schema_for_provider_type(): non-ProviderRegistration reg → [],
    strategy_class is None → [], schema exception → [] (lines 75-104)
  - get_all_provider_schemas(): empty registry, schema exception per provider (lines 117-139)
  - get_provider_schema(): provider not found → 404, schema exception → [] (lines 151-173)
  - list_providers(): no provider config, active_providers raises, normal list (lines 186-237)
  - get_providers_health(): default_provider attribute raises (lines 271-278)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import get_config_manager, get_current_user
from orb.api.routers.providers import router as providers_router

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_app(*, role: str = "viewer") -> FastAPI:
    from fastapi.responses import JSONResponse

    from orb.api.dependencies import CurrentUser
    from orb.infrastructure.error.exception_handler import get_exception_handler

    app = FastAPI()
    app.include_router(providers_router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="test-user", role=role
    )

    exception_handler = get_exception_handler()

    @app.exception_handler(Exception)
    async def _handler(__request, exc):
        from fastapi import HTTPException

        if isinstance(exc, HTTPException):
            raise exc
        error_response = exception_handler.handle_error_for_http(exc)
        return JSONResponse(
            status_code=error_response.http_status or 500,
            content={"detail": error_response.message},
        )

    return app


def _provider_instance(*, name: str, ptype: str = "aws", enabled: bool = True):
    p = MagicMock()
    p.name = name
    p.type = ptype
    p.enabled = enabled
    p.config = {"region": "us-east-1"}
    return p


def _config_mgr_no_providers():
    provider_config = MagicMock()
    provider_config.get_active_providers.return_value = []
    provider_config.default_provider = None
    m = MagicMock()
    m.get_provider_config.return_value = provider_config
    return m


def _config_mgr_with(*providers):
    provider_config = MagicMock()
    provider_config.get_active_providers.return_value = list(providers)
    provider_config.default_provider = providers[0].name if providers else None
    m = MagicMock()
    m.get_provider_config.return_value = provider_config
    return m


# ---------------------------------------------------------------------------
# _probe_provider_health()
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestProbeProviderHealth:
    def test_exception_returns_unknown_status(self):
        from orb.api.routers.providers import _probe_provider_health

        with patch("orb.api.routers.providers.get_di_container") as mock_ctr:
            mock_ctr.side_effect = RuntimeError("container unavailable")
            status, details = asyncio.run(_probe_provider_health("my-provider"))

        assert status == "unknown"
        assert details == {}

    def test_unhealthy_result_returns_degraded(self):
        from orb.api.routers.providers import _probe_provider_health

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.data = None
        mock_result.error_message = "unhealthy"

        mock_registry = MagicMock()
        mock_registry.execute_operation = AsyncMock(return_value=mock_result)

        mock_container = MagicMock()
        mock_container.get.return_value = mock_registry

        with patch("orb.api.routers.providers.get_di_container", return_value=mock_container):
            status, details = asyncio.run(_probe_provider_health("prov"))

        assert status == "degraded"
        assert details == {}

    def test_healthy_result_with_response_time_and_status_message(self):
        from orb.api.routers.providers import _probe_provider_health

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = {
            "is_healthy": True,
            "response_time_ms": 42,
            "status_message": "OK",
        }

        mock_registry = MagicMock()
        mock_registry.execute_operation = AsyncMock(return_value=mock_result)

        mock_container = MagicMock()
        mock_container.get.return_value = mock_registry

        with patch("orb.api.routers.providers.get_di_container", return_value=mock_container):
            status, details = asyncio.run(_probe_provider_health("prov"))

        assert status == "healthy"
        assert details["response_time_ms"] == 42
        assert details["status_message"] == "OK"

    def test_healthy_true_without_extras_returns_no_details(self):
        from orb.api.routers.providers import _probe_provider_health

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = {"is_healthy": True}

        mock_registry = MagicMock()
        mock_registry.execute_operation = AsyncMock(return_value=mock_result)

        mock_container = MagicMock()
        mock_container.get.return_value = mock_registry

        with patch("orb.api.routers.providers.get_di_container", return_value=mock_container):
            status, details = asyncio.run(_probe_provider_health("prov"))

        assert status == "healthy"
        assert "response_time_ms" not in details

    def test_is_healthy_false_returns_degraded(self):
        from orb.api.routers.providers import _probe_provider_health

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = {"is_healthy": False}

        mock_registry = MagicMock()
        mock_registry.execute_operation = AsyncMock(return_value=mock_result)

        mock_container = MagicMock()
        mock_container.get.return_value = mock_registry

        with patch("orb.api.routers.providers.get_di_container", return_value=mock_container):
            status, details = asyncio.run(_probe_provider_health("prov"))

        assert status == "degraded"


# ---------------------------------------------------------------------------
# _get_schema_for_provider_type()
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestGetSchemaForProviderType:
    def test_non_provider_registration_returns_empty_list(self):
        from orb.api.routers.providers import _get_schema_for_provider_type

        mock_registry = MagicMock()
        # Return an object that is NOT an instance of ProviderRegistration
        mock_registry._get_type_registration.return_value = MagicMock(spec=[])

        with patch(
            "orb.providers.registry.provider_registry.get_provider_registry",
            return_value=mock_registry,
        ):
            result = _get_schema_for_provider_type("unknown-type")

        assert result == []

    def test_strategy_class_none_returns_empty_list(self):
        from orb.api.routers.providers import _get_schema_for_provider_type
        from orb.providers.registry.types import ProviderRegistration

        reg = MagicMock(spec=ProviderRegistration)
        reg.strategy_class = None

        mock_registry = MagicMock()
        mock_registry._get_type_registration.return_value = reg

        with patch(
            "orb.providers.registry.provider_registry.get_provider_registry",
            return_value=mock_registry,
        ):
            result = _get_schema_for_provider_type("notype")

        assert result == []

    def test_schema_exception_returns_empty_list(self):
        from orb.api.routers.providers import _get_schema_for_provider_type
        from orb.providers.registry.types import ProviderRegistration

        mock_strategy_cls = MagicMock()
        mock_strategy_cls.get_ui_column_schema.side_effect = RuntimeError("schema error")

        reg = MagicMock(spec=ProviderRegistration)
        reg.strategy_class = mock_strategy_cls

        mock_registry = MagicMock()
        mock_registry._get_type_registration.return_value = reg

        with patch(
            "orb.providers.registry.provider_registry.get_provider_registry",
            return_value=mock_registry,
        ):
            result = _get_schema_for_provider_type("badtype")

        assert result == []


# ---------------------------------------------------------------------------
# GET /providers/schemas
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestGetAllProviderSchemas:
    def test_empty_registry_returns_empty_schemas(self):
        mock_registry = MagicMock()
        mock_registry.get_registered_providers.return_value = []

        app = _make_app()

        with patch(
            "orb.providers.registry.provider_registry.get_provider_registry",
            return_value=mock_registry,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/providers/schemas")

        assert resp.status_code == 200
        body = resp.json()
        assert body["schemas"] == {}
        assert body["schema_version"] == 1

    def test_schema_header_present(self):
        mock_registry = MagicMock()
        mock_registry.get_registered_providers.return_value = []

        app = _make_app()

        with patch(
            "orb.providers.registry.provider_registry.get_provider_registry",
            return_value=mock_registry,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/providers/schemas")

        assert resp.headers.get("x-schema-version") == "1"

    def test_schema_exception_per_provider_returns_empty_list_for_that_provider(self):
        mock_registry = MagicMock()
        mock_registry.get_registered_providers.return_value = ["aws"]

        app = _make_app()

        with (
            patch(
                "orb.providers.registry.provider_registry.get_provider_registry",
                return_value=mock_registry,
            ),
            patch(
                "orb.api.routers.providers._get_schema_for_provider_type",
                side_effect=RuntimeError("schema fetch error"),
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/providers/schemas")

        assert resp.status_code == 200
        body = resp.json()
        assert body["schemas"]["aws"] == []

    def test_viewer_role_allowed(self):
        mock_registry = MagicMock()
        mock_registry.get_registered_providers.return_value = []

        app = _make_app(role="viewer")

        with patch(
            "orb.providers.registry.provider_registry.get_provider_registry",
            return_value=mock_registry,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/providers/schemas")

        assert resp.status_code == 200

    def test_forbidden_role_denied(self):
        app = _make_app(role="noaccess")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/providers/schemas")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /providers/{name}/schema
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestGetProviderSchema:
    def test_unknown_provider_returns_404(self):
        mock_registry = MagicMock()
        mock_registry.is_provider_registered.return_value = False

        app = _make_app()

        with patch(
            "orb.providers.registry.provider_registry.get_provider_registry",
            return_value=mock_registry,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/providers/unknown-prov/schema")

        assert resp.status_code == 404

    def test_known_provider_returns_schema(self):
        mock_registry = MagicMock()
        mock_registry.is_provider_registered.return_value = True

        app = _make_app()

        with (
            patch(
                "orb.providers.registry.provider_registry.get_provider_registry",
                return_value=mock_registry,
            ),
            patch(
                "orb.api.routers.providers._get_schema_for_provider_type",
                return_value=[{"id": "col1", "label": "Col 1"}],
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/providers/aws/schema")

        assert resp.status_code == 200
        body = resp.json()
        assert body["schema_version"] == 1
        assert len(body["schema"]) == 1

    def test_schema_exception_returns_empty_schema(self):
        mock_registry = MagicMock()
        mock_registry.is_provider_registered.return_value = True

        app = _make_app()

        with (
            patch(
                "orb.providers.registry.provider_registry.get_provider_registry",
                return_value=mock_registry,
            ),
            patch(
                "orb.api.routers.providers._get_schema_for_provider_type",
                side_effect=RuntimeError("boom"),
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/providers/aws/schema")

        assert resp.status_code == 200
        assert resp.json()["schema"] == []


# ---------------------------------------------------------------------------
# GET /providers/ (list_providers)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestListProviders:
    def test_no_provider_config_returns_empty_list(self):
        config_mgr = MagicMock()
        config_mgr.get_provider_config.return_value = None

        app = _make_app()
        app.dependency_overrides[get_config_manager] = lambda: config_mgr

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/providers/")

        assert resp.status_code == 200
        assert resp.json()["providers"] == []
        assert resp.json()["total_count"] == 0

    def test_active_providers_exception_returns_empty_list(self):
        provider_config = MagicMock()
        provider_config.get_active_providers.side_effect = RuntimeError("provider list error")

        config_mgr = MagicMock()
        config_mgr.get_provider_config.return_value = provider_config

        app = _make_app()
        app.dependency_overrides[get_config_manager] = lambda: config_mgr

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/providers/")

        assert resp.status_code == 200
        assert resp.json()["providers"] == []

    def test_returns_configured_providers(self):
        p1 = _provider_instance(name="aws-main", ptype="aws")
        config_mgr = _config_mgr_with(p1)

        app = _make_app()
        app.dependency_overrides[get_config_manager] = lambda: config_mgr

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/providers/")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_count"] == 1
        assert body["providers"][0]["name"] == "aws-main"
        assert body["providers"][0]["type"] == "aws"

    def test_provider_config_exception_returns_empty_list(self):
        config_mgr = MagicMock()
        config_mgr.get_provider_config.side_effect = RuntimeError("config error")

        app = _make_app()
        app.dependency_overrides[get_config_manager] = lambda: config_mgr

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/providers/")

        assert resp.status_code == 200
        assert resp.json()["providers"] == []


# ---------------------------------------------------------------------------
# GET /providers/health — default_provider attribute raises
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestProvidersHealthDefaultProviderRaises:
    def test_default_provider_attr_raises_still_returns_200(self):
        provider_config = MagicMock(spec=["get_active_providers"])
        # Accessing .default_provider raises AttributeError
        provider_config.get_active_providers.return_value = []

        # Manually simulate the attribute raising via __getattr__
        type(provider_config).default_provider = property(
            lambda self: (_ for _ in ()).throw(AttributeError("no default_provider"))
        )

        config_mgr = MagicMock()
        config_mgr.get_provider_config.return_value = provider_config

        app = _make_app()
        app.dependency_overrides[get_config_manager] = lambda: config_mgr

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/providers/health")
        assert resp.status_code == 200

    def test_multiple_providers_first_enabled_marked_active(self):
        p1 = _provider_instance(name="p1", enabled=True)
        p2 = _provider_instance(name="p2", enabled=True)

        config_mgr = _config_mgr_with(p1, p2)

        app = _make_app()
        app.dependency_overrides[get_config_manager] = lambda: config_mgr

        with patch(
            "orb.api.routers.providers._probe_provider_health",
            new=AsyncMock(return_value=("healthy", {})),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/providers/health")

        assert resp.status_code == 200
        providers = resp.json()["providers"]
        assert len(providers) == 2
        # At most one provider should be active
        active_providers = [p for p in providers if p["active"]]
        assert len(active_providers) == 1
        assert active_providers[0]["name"] == "p1"
