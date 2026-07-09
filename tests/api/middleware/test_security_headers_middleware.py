"""Tests for SecurityHeadersMiddleware."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.middleware.security_headers_middleware import SecurityHeadersMiddleware


def _make_app(require_https: bool = False) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, require_https=require_https)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


EXPECTED_HEADERS = {
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "content-security-policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        # ws: and wss: are intentionally included to allow WebSocket connections
        # from the Reflex UI component; the header is otherwise unchanged.
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'"
    ),
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "geolocation=(), microphone=(), camera=(), payment=()",
}


class TestSecurityHeadersPresent:
    """Security headers are present on every response."""

    def test_all_hardening_headers_present(self):
        client = TestClient(_make_app())
        resp = client.get("/ping")
        assert resp.status_code == 200
        for header, value in EXPECTED_HEADERS.items():
            assert resp.headers.get(header) == value, (
                f"Missing or wrong value for {header!r}: got {resp.headers.get(header)!r}"
            )

    def test_no_hsts_without_require_https(self):
        client = TestClient(_make_app(require_https=False))
        resp = client.get("/ping")
        assert "strict-transport-security" not in resp.headers

    def test_hsts_present_with_require_https(self):
        client = TestClient(_make_app(require_https=True))
        resp = client.get("/ping")
        hsts = resp.headers.get("strict-transport-security", "")
        assert "max-age=31536000" in hsts
        assert "includeSubDomains" in hsts

    def test_headers_present_on_404(self):
        """Non-existent routes still carry security headers."""
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/not-found")
        for header in EXPECTED_HEADERS:
            assert header in resp.headers, f"Missing {header!r} on 404 response"

    def test_headers_present_when_auth_disabled(self):
        """Headers are present regardless of auth configuration (regression guard)."""
        # The SecurityHeadersMiddleware is unconditional — this test documents
        # that headers do not depend on AuthMiddleware being registered at all.
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)

        @app.get("/open")
        def open_route():
            return {"public": True}

        client = TestClient(app)
        resp = client.get("/open")
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_x_xss_protection_not_emitted(self):
        """X-XSS-Protection is intentionally omitted (deprecated header)."""
        client = TestClient(_make_app())
        resp = client.get("/ping")
        assert "x-xss-protection" not in resp.headers
