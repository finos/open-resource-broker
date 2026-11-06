"""The auth middleware fails closed on its own errors and lets route errors through.

Route errors reach the application's exception handlers instead of being reported as
authentication failures; an error inside the authentication stage never lets the
request reach the route.
"""

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.middleware.auth_middleware import AuthMiddleware


class TestAuthMiddlewareFailClosedAndRouteErrors:
    """Route errors reach the app's exception handler; auth-stage errors fail closed."""

    SECRET = "middleware-test-secret-key-0123456789"

    def _strategy(self):
        from orb.infrastructure.auth.strategy.bearer_token_strategy import BearerTokenStrategy

        return BearerTokenStrategy(secret_key=self.SECRET, algorithm="HS256", enabled=True)

    def _token(self, strategy) -> str:
        return strategy._create_access_token(
            user_id="test-user", roles=["user"], permissions=["read"]
        )

    def _make_app(self, strategy, audit=None):
        from orb.domain.base.exceptions import EntityNotFoundError

        app = FastAPI()
        calls = {"route": 0}
        app.add_middleware(
            AuthMiddleware,
            auth_port=strategy,
            require_auth=True,
            audit_logger=audit or MagicMock(),
        )

        @app.exception_handler(Exception)
        async def handler(request, exc):
            from fastapi.responses import JSONResponse

            status_code = 404 if isinstance(exc, EntityNotFoundError) else 500
            return JSONResponse({"success": False, "error": {"code": "X"}}, status_code=status_code)

        @app.get("/missing")
        def missing():
            raise EntityNotFoundError("Request", "req-1")

        @app.get("/ok")
        def ok():
            calls["route"] += 1
            return {"ok": True}

        return app, calls

    def test_route_not_found_error_is_not_reported_as_middleware_failure(self, caplog):
        import logging

        strategy = self._strategy()
        app, _ = self._make_app(strategy)
        client = TestClient(app, raise_server_exceptions=False)

        with caplog.at_level(logging.ERROR):
            response = client.get(
                "/missing", headers={"Authorization": f"Bearer {self._token(strategy)}"}
            )

        assert response.status_code == 404
        assert "Authentication middleware error" not in caplog.text

    def test_authenticate_error_returns_500_without_calling_route(self, monkeypatch):
        strategy = self._strategy()
        token = self._token(strategy)
        app, calls = self._make_app(strategy)

        async def boom(context):
            raise RuntimeError("auth backend down")

        monkeypatch.setattr(strategy, "authenticate", boom)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/ok", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 500
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert "auth backend down" not in response.text
        assert calls["route"] == 0

    def test_audit_logger_error_returns_500_without_calling_route(self):
        strategy = self._strategy()
        audit = MagicMock()
        audit.log_auth_success.side_effect = RuntimeError("audit sink down")
        app, calls = self._make_app(strategy, audit=audit)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/ok", headers={"Authorization": f"Bearer {self._token(strategy)}"})

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert calls["route"] == 0

    def test_invalid_token_is_still_401_without_calling_route(self):
        strategy = self._strategy()
        app, calls = self._make_app(strategy)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/ok", headers={"Authorization": "Bearer not-a-token"})

        assert response.status_code == 401
        assert calls["route"] == 0
