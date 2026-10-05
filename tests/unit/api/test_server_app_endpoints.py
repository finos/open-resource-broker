"""Unit tests for ``create_fastapi_app`` wiring: config validation, the global
exception handler, and the standalone system endpoints (/info, /metrics).

These exercise the factory's own logic (not the routers) so they run without a
DI container or provider backend.  Routers are stubbed out so the app builds
with just the system endpoints under test.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from orb.api.server import create_fastapi_app
from orb.config.schemas.server_schema import AuthConfig, ServerConfig
from orb.domain.base.exceptions import ConfigurationError


def _make_app(**overrides):
    server_config = ServerConfig(  # type: ignore[call-arg]
        enabled=True,
        auth=AuthConfig(enabled=False, strategy="replace"),  # type: ignore[call-arg]
        **overrides,
    )

    def _stub_routes(app):
        app.include_router(APIRouter())

    with patch("orb.api.server._register_routers") as mock_register:
        mock_register.side_effect = _stub_routes
        return create_fastapi_app(server_config)


@pytest.mark.unit
@pytest.mark.api
class TestConfigValidation:
    def test_none_config_raises_configuration_error(self):
        with pytest.raises(ConfigurationError, match="requires a ServerConfig"):
            create_fastapi_app(None)

    def test_invalid_config_object_raises_configuration_error(self):
        """An object missing 'docs_enabled' is rejected rather than booting fail-open."""

        class _NotAServerConfig:
            pass

        with pytest.raises(ConfigurationError, match="missing required attribute"):
            create_fastapi_app(_NotAServerConfig())


@pytest.mark.unit
@pytest.mark.api
class TestSystemEndpoints:
    def test_info_endpoint_returns_service_metadata(self):
        client = TestClient(_make_app())
        resp = client.get("/info")
        assert resp.status_code == 200
        body = resp.json()
        assert body["service"] == "open-resource-broker"
        assert "version" in body
        # Auth configuration must NOT be disclosed to unauthenticated callers.
        assert "auth" not in body

    def test_metrics_endpoint_returns_plaintext(self):
        client = TestClient(_make_app())
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")

    def test_metrics_endpoint_empty_when_prometheus_absent(self):
        """A minimal install without prometheus_client serves an empty 200."""
        client = TestClient(_make_app())
        with patch.dict("sys.modules", {"prometheus_client": None}):
            resp = client.get("/metrics")
        assert resp.status_code == 200


@pytest.mark.unit
@pytest.mark.api
class TestFaviconEndpoint:
    """The favicon is read once from a packaged resource at app creation,
    never from request-controllable input, so there is no path for a
    traversal attempt to reach a file outside the packaged asset."""

    def test_favicon_serves_the_packaged_icon(self):
        client = TestClient(_make_app())
        resp = client.get("/favicon.ico")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content.startswith(b"\x89PNG")

    @pytest.mark.parametrize(
        "suffix",
        [
            "/../../../../etc/passwd",
            "/..%2f..%2f..%2fetc%2fpasswd",
            "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "//etc/passwd",
            "/etc/passwd",
        ],
    )
    def test_favicon_path_traversal_attempts_do_not_escape_the_asset(self, suffix):
        client = TestClient(_make_app())
        resp = client.get(f"/favicon.ico{suffix}", follow_redirects=False)
        # The route matches only the exact literal path, so any traversal
        # attempt simply fails to match a route instead of ever reaching
        # the filesystem.
        assert resp.status_code == 404
        if resp.headers.get("content-type") == "image/png":
            pytest.fail("traversal attempt unexpectedly returned image content")


@pytest.mark.unit
@pytest.mark.api
class TestGlobalExceptionHandler:
    def test_unhandled_route_exception_maps_to_structured_error(self):
        """An exception raised in a route is caught by the global handler and
        rendered as the structured error envelope (not a bare 500)."""
        app = _make_app()

        @app.get("/boom")
        async def _boom():
            raise ValueError("kaboom")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/boom")

        assert resp.status_code >= 400
        body = resp.json()
        assert body["success"] is False
        assert "error" in body
        assert "code" in body["error"]


@pytest.mark.unit
@pytest.mark.api
class TestMiddlewareWiring:
    def test_read_only_mode_builds(self):
        """read_only=True installs the read-only middleware without error."""
        app = _make_app(read_only=True)
        client = TestClient(app)
        # A safe GET still works; the app booted with the middleware attached.
        assert client.get("/info").status_code == 200

    def test_cors_wildcard_with_auth_enabled_builds(self):
        """CORS origins=['*'] with auth enabled logs a warning but still boots."""
        from orb.config.schemas.server_schema import CORSConfig

        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(  # type: ignore[call-arg]
                enabled=True,
                strategy="bearer_token",
                bearer_token={"secret_key": "unit-test-secret-key-very-long-and-secure"},
            ),
            cors=CORSConfig(enabled=True, origins=["*"], credentials=False),  # type: ignore[call-arg]
        )

        def _stub_routes(app):
            app.include_router(APIRouter())

        with patch("orb.api.server._register_routers") as mock_register:
            mock_register.side_effect = _stub_routes
            app = create_fastapi_app(server_config)

        client = TestClient(app)
        # /health is an excluded (public) path even with auth enabled.
        assert client.get("/health").status_code in (200, 503)

    def test_multi_worker_warnings_do_not_block_boot(self):
        """workers>1 emits rate-limit + SSE multi-worker warnings but still boots."""
        app = _make_app(workers=4)
        client = TestClient(app)
        assert client.get("/info").status_code == 200


@pytest.mark.unit
@pytest.mark.api
class TestTrustedHostsWiring:
    def test_wildcard_trusted_hosts_skips_host_middleware(self):
        """A '*' allowlist disables Host-header validation (warned, not enforced)."""
        app = _make_app(trusted_hosts=["*"])
        client = TestClient(app, base_url="http://evil.example.com")
        # With host protection disabled, an arbitrary Host is still served.
        resp = client.get("/info")
        assert resp.status_code == 200

    def test_explicit_trusted_hosts_rejects_unknown_host(self):
        app = _make_app(trusted_hosts=["allowed.example.com"])
        client = TestClient(app, base_url="http://evil.example.com")
        resp = client.get("/info")
        # TrustedHostMiddleware returns 400 for a Host not on the allowlist.
        assert resp.status_code == 400
