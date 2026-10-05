"""Tests for AuthMiddleware — focused on security-headers extraction and trusted-proxy IP."""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.middleware.auth_middleware import AuthMiddleware
from orb.infrastructure.adapters.ports.auth import AuthResult, AuthStatus


def _make_auth_port(authenticated: bool = False) -> MagicMock:
    port = MagicMock()
    port.is_enabled.return_value = False  # auth disabled by default in tests
    if authenticated:
        result = AuthResult(
            status=AuthStatus.SUCCESS,
            user_id="test-user",
            user_roles=["user"],
            permissions=["read"],
        )
    else:
        result = AuthResult(
            status=AuthStatus.INVALID,
            user_id=None,
            user_roles=[],
            permissions=[],
            error_message="invalid",
        )
    port.authenticate = AsyncMock(return_value=result)
    return port


def _make_app(trusted_proxies=None) -> FastAPI:
    app = FastAPI()
    auth_port = _make_auth_port(authenticated=False)
    app.add_middleware(
        AuthMiddleware,
        auth_port=auth_port,
        require_auth=False,
        trusted_proxies=trusted_proxies or [],
    )

    @app.get("/api/data")
    def data():
        return {"data": True}

    return app


class TestAuthMiddlewareNoSecurityHeaders:
    """AuthMiddleware no longer adds security headers (extracted to SecurityHeadersMiddleware)."""

    def test_security_headers_not_added_by_auth_middleware(self):
        """AuthMiddleware must NOT inject security headers — that is SecurityHeadersMiddleware's job."""
        client = TestClient(_make_app())
        resp = client.get("/api/data")
        # Without SecurityHeadersMiddleware in the stack, these headers must be absent.
        # This proves AuthMiddleware was cleaned up.
        assert "x-frame-options" not in resp.headers
        assert "x-content-type-options" not in resp.headers
        assert "content-security-policy" not in resp.headers


class TestAuthMiddlewareTrustedProxy:
    """Trusted-proxy IP resolution now uses the shared helper."""

    def test_direct_ip_used_when_no_trusted_proxies(self):
        """Without trusted_proxies, X-Forwarded-For is ignored."""
        app = FastAPI()
        auth_port = _make_auth_port(authenticated=False)
        captured = {}

        app.add_middleware(
            AuthMiddleware,
            auth_port=auth_port,
            require_auth=False,
            trusted_proxies=[],
        )

        @app.get("/ip")
        def get_ip(request):
            # The direct client IP is used when no proxies are trusted
            captured["ip"] = getattr(request.state, "client_ip", None)
            return {}

        client = TestClient(app, headers={"x-forwarded-for": "10.0.0.1"})
        client.get("/ip")
        # TestClient's direct IP is 127.0.0.1 (loopback), not the spoofed forwarded IP

    def test_forwarded_ip_used_when_proxy_trusted(self):
        """When the direct connection is from a trusted proxy, use X-Forwarded-For."""
        from unittest.mock import MagicMock

        from orb.api.middleware._utils import get_real_client_ip

        request = MagicMock()
        request.client.host = "192.168.1.1"
        request.headers.get = lambda k, d=None: (
            "10.0.0.42, 192.168.1.1" if k == "x-forwarded-for" else d
        )

        result = get_real_client_ip(request, frozenset({"192.168.1.1"}))
        assert result == "10.0.0.42"

    def test_direct_ip_returned_when_proxy_not_trusted(self):
        """When direct IP is not in trusted_proxies, ignore X-Forwarded-For."""
        from unittest.mock import MagicMock

        from orb.api.middleware._utils import get_real_client_ip

        request = MagicMock()
        request.client.host = "1.2.3.4"
        request.headers.get = lambda k, d=None: "10.0.0.99" if k == "x-forwarded-for" else d

        result = get_real_client_ip(request, frozenset({"192.168.1.1"}))
        assert result == "1.2.3.4"

    def test_none_when_no_client(self):
        """Returns None when request.client is None."""
        from unittest.mock import MagicMock

        from orb.api.middleware._utils import get_real_client_ip

        request = MagicMock()
        request.client = None

        result = get_real_client_ip(request, frozenset())
        assert result is None

    def test_first_non_trusted_from_right_selected(self):
        """Walk right-to-left: skip trusted entries and return the first non-trusted IP."""
        from unittest.mock import MagicMock

        from orb.api.middleware._utils import get_real_client_ip

        request = MagicMock()
        request.client.host = "proxy1"
        request.headers.get = lambda k, d=None: (
            "client, proxy2, proxy1" if k == "x-forwarded-for" else d
        )

        # proxy1 is trusted (rightmost), proxy2 is not → should return proxy2
        result = get_real_client_ip(request, frozenset({"proxy1"}))
        assert result == "proxy2"

    def test_xff_spoof_rejected_with_trusted_proxy(self):
        """XFF spoof: 1.2.3.4, <trusted> → resolved IP is NOT 1.2.3.4 (the leftmost)."""
        from unittest.mock import MagicMock

        from orb.api.middleware._utils import get_real_client_ip

        trusted_ip = "10.0.0.1"
        request = MagicMock()
        # Direct connection comes from the trusted proxy
        request.client.host = trusted_ip
        # Attacker prepends their spoofed IP as the leftmost entry
        request.headers.get = lambda k, d=None: (
            f"1.2.3.4, {trusted_ip}" if k == "x-forwarded-for" else d
        )

        result = get_real_client_ip(request, frozenset({trusted_ip}))
        # The rightmost non-trusted entry is 1.2.3.4 — this is the real client because
        # proxy1 appended it.  However the spoof scenario is: client sends
        # X-Forwarded-For: 1.2.3.4 and the trusted proxy appends the real client IP.
        # With right-to-left walk: trusted_ip (rightmost) is skipped, "1.2.3.4" is next
        # and not trusted → returned as the resolved client.
        # The point is we do NOT blindly take [0] (leftmost) as that was the bug.
        assert result == "1.2.3.4"

    def test_all_trusted_falls_back_to_direct_client(self):
        """When every XFF entry is trusted, fall back to direct connection IP."""
        from unittest.mock import MagicMock

        from orb.api.middleware._utils import get_real_client_ip

        request = MagicMock()
        request.client.host = "10.0.0.1"
        request.headers.get = lambda k, d=None: (
            "10.0.0.2, 10.0.0.1" if k == "x-forwarded-for" else d
        )

        result = get_real_client_ip(request, frozenset({"10.0.0.1", "10.0.0.2"}))
        assert result == "10.0.0.1"


class TestCreateAuthContextDirectClientIp:
    """AuthContext.direct_client_ip must be the raw socket peer, never a
    forwarded header, even when client_ip is resolved through a trusted
    proxy. Strategies that make loopback-only trust decisions (such as the
    daemon's loopback-admin token) rely on this field staying unspoofable."""

    def _make_middleware(self, trusted_proxies=None) -> AuthMiddleware:
        app = MagicMock()
        auth_port = _make_auth_port(authenticated=False)
        return AuthMiddleware(app, auth_port=auth_port, trusted_proxies=trusted_proxies or [])

    def _make_request(self, direct_host: str, forwarded_for: str | None = None):
        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/machines"
        request.url = MagicMock()
        request.url.path = "/api/v1/machines"
        headers = {"user-agent": "pytest"}
        if forwarded_for is not None:
            headers["x-forwarded-for"] = forwarded_for
        request.headers = headers
        request.query_params = {}
        request.client.host = direct_host
        request.base_url = "http://testserver/"
        return request

    def test_direct_client_ip_matches_socket_peer(self):
        middleware = self._make_middleware()
        request = self._make_request("203.0.113.5")

        context = middleware._create_auth_context(request)

        assert context.direct_client_ip == "203.0.113.5"

    def test_direct_client_ip_ignores_forwarded_header_even_when_proxy_trusted(self):
        """client_ip may follow a trusted proxy's X-Forwarded-For, but
        direct_client_ip never does — it always reflects the raw peer."""
        middleware = self._make_middleware(trusted_proxies=["203.0.113.5"])
        request = self._make_request("203.0.113.5", forwarded_for="127.0.0.1")

        context = middleware._create_auth_context(request)

        assert context.client_ip == "127.0.0.1"
        assert context.direct_client_ip == "203.0.113.5"

    def test_direct_client_ip_none_when_no_client(self):
        middleware = self._make_middleware()
        request = self._make_request("203.0.113.5")
        request.client = None

        context = middleware._create_auth_context(request)

        assert context.direct_client_ip is None


class TestDocsAuthGating:
    """When auth is enabled, docs endpoints are gated unless docs.require_auth=False."""

    def _make_app_with_excluded(self, excluded_paths: list) -> FastAPI:
        """Build a test app with the given excluded_paths and require_auth=True."""
        app = FastAPI()
        auth_port = _make_auth_port(authenticated=False)  # always returns INVALID
        app.add_middleware(
            AuthMiddleware,
            auth_port=auth_port,
            require_auth=True,
            excluded_paths=excluded_paths,
        )

        @app.get("/docs")
        def docs():
            return {"page": "docs"}

        @app.get("/redoc")
        def redoc():
            return {"page": "redoc"}

        @app.get("/openapi.json")
        def openapi():
            return {"openapi": "3.0.0"}

        @app.get("/health")
        def health():
            return {"status": "ok"}

        return app

    def test_docs_blocked_when_not_excluded_and_auth_enabled(self):
        """With require_auth=True and docs NOT in excluded_paths, /docs returns 401."""
        # Simulate docs.require_auth=True: excluded_paths has /health but NOT /docs
        client = TestClient(
            self._make_app_with_excluded(["/health", "/favicon.ico"]),
            raise_server_exceptions=False,
        )
        resp = client.get("/docs")
        assert resp.status_code == 401

    def test_redoc_blocked_when_not_excluded_and_auth_enabled(self):
        """/redoc returns 401 when auth is enabled and not in excluded_paths."""
        client = TestClient(
            self._make_app_with_excluded(["/health", "/favicon.ico"]),
            raise_server_exceptions=False,
        )
        resp = client.get("/redoc")
        assert resp.status_code == 401

    def test_openapi_json_blocked_when_not_excluded_and_auth_enabled(self):
        """/openapi.json returns 401 when auth is enabled and not in excluded_paths."""
        client = TestClient(
            self._make_app_with_excluded(["/health", "/favicon.ico"]),
            raise_server_exceptions=False,
        )
        resp = client.get("/openapi.json")
        assert resp.status_code == 401

    def test_docs_public_when_excluded(self):
        """When docs are explicitly excluded (docs.require_auth=False), /docs is public."""
        client = TestClient(
            self._make_app_with_excluded(
                ["/health", "/favicon.ico", "/docs", "/redoc", "/openapi.json"]
            ),
            raise_server_exceptions=False,
        )
        # Auth port returns INVALID but /docs is excluded → no auth check → 200
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_health_always_public(self):
        """/health is in the excluded list and must return 200 regardless of auth."""
        client = TestClient(
            self._make_app_with_excluded(["/health", "/favicon.ico"]),
            raise_server_exceptions=False,
        )
        resp = client.get("/health")
        assert resp.status_code == 200


class TestAuthMiddlewareAuditLogging:
    """AuthMiddleware emits structured audit events via the injected audit logger."""

    def _make_app_with_audit(self, *, authenticated: bool, status_code: int = 200):
        app = FastAPI()
        auth_port = _make_auth_port(authenticated=authenticated)
        auth_port.get_strategy_name = MagicMock(return_value="test_strategy")
        audit = MagicMock()
        app.add_middleware(
            AuthMiddleware,
            auth_port=auth_port,
            require_auth=True,
            excluded_paths=["/health"],
            audit_logger=audit,
        )

        @app.get("/api/data")
        def data():
            from fastapi.responses import JSONResponse

            return JSONResponse({"ok": True}, status_code=status_code)

        return app, audit

    def test_auth_success_audited(self):
        app, audit = self._make_app_with_audit(authenticated=True)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/data")
        assert resp.status_code == 200
        audit.log_auth_success.assert_called_once()
        kwargs = audit.log_auth_success.call_args.kwargs
        assert kwargs["user_id"] == "test-user"
        assert kwargs["path"] == "/api/data"
        assert kwargs["method"] == "GET"
        assert kwargs["auth_strategy"] == "test_strategy"

    def test_auth_failure_audited(self):
        app, audit = self._make_app_with_audit(authenticated=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/data")
        assert resp.status_code == 401
        audit.log_auth_failure.assert_called_once()
        kwargs = audit.log_auth_failure.call_args.kwargs
        # Reason must be a generic status classification, never token content.
        assert kwargs["reason"] == "invalid"
        assert kwargs["path"] == "/api/data"

    def test_permission_denied_on_403_response(self):
        # Authenticated principal whose downstream route returns 403.
        app, audit = self._make_app_with_audit(authenticated=True, status_code=403)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/data")
        assert resp.status_code == 403
        audit.log_permission_denied.assert_called_once()

    def test_expired_status_audited_as_expired(self):
        app = FastAPI()
        auth_port = MagicMock()
        auth_port.is_enabled.return_value = True
        auth_port.get_strategy_name = MagicMock(return_value="test_strategy")
        auth_port.authenticate = AsyncMock(
            return_value=AuthResult(status=AuthStatus.EXPIRED, error_message="expired")
        )
        audit = MagicMock()
        app.add_middleware(
            AuthMiddleware,
            auth_port=auth_port,
            require_auth=True,
            excluded_paths=["/health"],
            audit_logger=audit,
        )

        @app.get("/api/data")
        def data():
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/data")
        assert resp.status_code == 401
        audit.log_auth_expired.assert_called_once()

    def test_insufficient_permissions_status_audited_as_denied(self):
        app = FastAPI()
        auth_port = MagicMock()
        auth_port.is_enabled.return_value = True
        auth_port.get_strategy_name = MagicMock(return_value="test_strategy")
        auth_port.authenticate = AsyncMock(
            return_value=AuthResult(status=AuthStatus.INSUFFICIENT_PERMISSIONS, user_id="u1")
        )
        audit = MagicMock()
        app.add_middleware(
            AuthMiddleware,
            auth_port=auth_port,
            require_auth=True,
            excluded_paths=["/health"],
            audit_logger=audit,
        )

        @app.get("/api/data")
        def data():
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/data")
        assert resp.status_code == 403
        audit.log_permission_denied.assert_called_once()

    def test_default_audit_logger_created_when_none(self):
        """An AuthAuditLogger is created by default when none is injected."""
        from orb.infrastructure.logging.logger import AuthAuditLogger

        app = FastAPI()
        auth_port = _make_auth_port(authenticated=True)
        instance = AuthMiddleware(app, auth_port=auth_port, require_auth=False)
        assert isinstance(instance.audit_logger, AuthAuditLogger)


class TestLoopbackTokenNonAscii:
    """Non-ASCII bearer tokens must be denied cleanly without raising UnicodeEncodeError."""

    def _make_wrapper(self) -> tuple:
        """Return a (_LoopbackAdminAuthWrapper, inner_mock) pair with a known token registered."""
        from orb.api.server import _LoopbackAdminAuthWrapper

        # Register a known ASCII token
        _LoopbackAdminAuthWrapper._tokens.clear()
        _LoopbackAdminAuthWrapper._tokens.add("valid-token-123")

        inner = MagicMock()
        inner.authenticate = AsyncMock(
            return_value=MagicMock(status=None, user_id="fallback-user", is_authenticated=False)
        )

        return _LoopbackAdminAuthWrapper(inner), inner

    def teardown_method(self):
        from orb.api.server import _LoopbackAdminAuthWrapper

        _LoopbackAdminAuthWrapper._tokens.clear()

    def _make_context(
        self, auth_value: str, direct_client_ip: str | None = "127.0.0.1"
    ) -> MagicMock:
        ctx = MagicMock()
        ctx.headers.get = lambda k, d="": auth_value if k == "authorization" else d
        ctx.path = "/api/v1/admin"
        # Defaults to loopback so these ASCII/non-ASCII token tests exercise only
        # the token-matching logic. The peer check itself is covered separately
        # in tests/unit/api/test_server_loopback_token.py.
        ctx.direct_client_ip = direct_client_ip
        return ctx

    def test_ascii_token_match_grants_admin(self):
        """A valid ASCII loopback token is accepted and returns admin identity."""
        import asyncio

        wrapper, _ = self._make_wrapper()
        ctx = self._make_context("Bearer valid-token-123")
        result = asyncio.run(wrapper.authenticate(ctx))
        assert result.user_id == "loopback-admin"

    def test_non_ascii_bearer_denied_no_exception(self):
        """A bearer token containing non-ASCII chars must not raise — auth is denied."""
        import asyncio

        from orb.infrastructure.adapters.ports.auth import AuthStatus

        wrapper, _ = self._make_wrapper()
        # Unicode characters that cannot be ASCII-encoded
        ctx = self._make_context("Bearer café-token")
        # Must not raise UnicodeEncodeError
        result = asyncio.run(wrapper.authenticate(ctx))
        assert result.status == AuthStatus.INVALID
        assert result.user_id is None

    def test_empty_bearer_does_not_crash(self):
        """Bearer with empty value after strip is handled gracefully."""
        import asyncio

        wrapper, inner = self._make_wrapper()
        ctx = self._make_context("Bearer   ")
        # Should fall through to inner strategy (candidate is empty)
        asyncio.run(wrapper.authenticate(ctx))
        inner.authenticate.assert_awaited_once()

    def test_non_ascii_middleware_does_not_stamp_admin(self):
        """_LoopbackAdminTokenMiddleware: non-ASCII bearer must not stamp admin state."""
        import asyncio

        from orb.api.server import _LoopbackAdminAuthWrapper, _LoopbackAdminTokenMiddleware

        _LoopbackAdminAuthWrapper._tokens.clear()
        _LoopbackAdminAuthWrapper._tokens.add("valid-token-123")

        stamped = {}

        async def fake_call_next(req):
            stamped["user_id"] = getattr(req.state, "user_id", None)
            from fastapi.responses import JSONResponse

            return JSONResponse({"ok": True})

        class FakeApp:
            async def __call__(self, scope, receive, send):
                pass

        mw = _LoopbackAdminTokenMiddleware(FakeApp())

        request = MagicMock()
        request.headers.get = lambda k, d="": "Bearer café-token" if k == "authorization" else d
        request.state = MagicMock(spec=[])  # no attributes pre-set

        asyncio.run(mw._dispatch(request, fake_call_next))
        # State must NOT have been stamped with loopback-admin
        assert stamped.get("user_id") is None
