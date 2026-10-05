"""Integration tests for the effective server middleware order.

Covers the three outcomes that depend on CORS being outermost and
RateLimitMiddleware being registered inside AuthMiddleware:

- A cross-origin preflight OPTIONS request gets a CORS response, not a 401,
  even when authentication is enabled.
- Rate limiting keys on the authenticated user, not the shared source IP —
  two different users behind the same connection get independent buckets.
- An anonymous caller still falls back to per-IP bucketing.
- Audit logging still records requests after the reorder.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from orb.api.server import create_fastapi_app
from orb.config.schemas.server_schema import AuthConfig, CORSConfig, RateLimitConfig, ServerConfig
from orb.infrastructure.auth.strategy.bearer_token_strategy import BearerTokenStrategy

pytestmark = [pytest.mark.integration, pytest.mark.api]

_SECRET = "test-secret-key-for-middleware-order-test!"


def _make_token(user_id: str) -> str:
    strategy = BearerTokenStrategy(secret_key=_SECRET, algorithm="HS256", enabled=True)
    return strategy._create_access_token(user_id=user_id, roles=["user"], permissions=["read"])


class TestCorsPreflightRunsBeforeAuth:
    def test_preflight_options_gets_cors_response_not_401(self):
        """A cross-origin preflight must be answered by CORS, even with auth on."""
        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(  # type: ignore[call-arg]
                enabled=True,
                strategy="bearer_token",
                bearer_token={"secret_key": _SECRET, "algorithm": "HS256"},
            ),
            cors=CORSConfig(origins=["https://ui.example.com"]),  # type: ignore[call-arg]
        )
        app = create_fastapi_app(server_config)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.options(
            "/api/v1/templates",
            headers={
                "Origin": "https://ui.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "https://ui.example.com"

    def test_non_preflight_request_without_credentials_still_requires_auth(self):
        """CORS being outermost must not bypass auth for a real (non-OPTIONS) request."""
        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(  # type: ignore[call-arg]
                enabled=True,
                strategy="bearer_token",
                bearer_token={"secret_key": _SECRET, "algorithm": "HS256"},
            ),
            cors=CORSConfig(origins=["https://ui.example.com"]),  # type: ignore[call-arg]
        )
        app = create_fastapi_app(server_config)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/info", headers={"Origin": "https://ui.example.com"})

        assert response.status_code == 401


class TestRateLimitSeesAuthenticatedIdentity:
    def test_two_users_behind_same_connection_get_independent_buckets(self):
        """RateLimit must key on user_id, not the shared client IP."""
        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(  # type: ignore[call-arg]
                enabled=True,
                strategy="bearer_token",
                bearer_token={"secret_key": _SECRET, "algorithm": "HS256"},
            ),
            cors=CORSConfig(origins=["*"]),  # type: ignore[call-arg]
            # burst == requests_per_minute == 2 gives each identity exactly two
            # tokens up front with no refill-clamp surprises, and the refill
            # rate is far too slow to replenish within the test.
            rate_limiting=RateLimitConfig(  # type: ignore[call-arg]
                enabled=True, requests_per_minute=2, burst=2
            ),
        )
        app = create_fastapi_app(server_config)
        # Every request below comes from the same TestClient connection, i.e.
        # the same source IP as far as the server is concerned.
        client = TestClient(app, raise_server_exceptions=False)

        headers_a = {"Authorization": f"Bearer {_make_token('user-a')}"}
        headers_b = {"Authorization": f"Bearer {_make_token('user-b')}"}

        # Exhaust user-a's two-token bucket.
        assert client.get("/info", headers=headers_a).status_code == 200
        assert client.get("/info", headers=headers_a).status_code == 200
        assert client.get("/info", headers=headers_a).status_code == 429

        # If identity resolution ran before Auth populated request.state.user_id
        # (the bug), this request would resolve to the same "ip:<addr>" bucket
        # already exhausted by user-a above and be rejected with 429 too.
        resp_b = client.get("/info", headers=headers_b)
        assert resp_b.status_code == 200

    def test_same_user_exceeding_quota_is_rate_limited(self):
        """Sanity check: the limiter still enforces a per-user cap."""
        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(  # type: ignore[call-arg]
                enabled=True,
                strategy="bearer_token",
                bearer_token={"secret_key": _SECRET, "algorithm": "HS256"},
            ),
            cors=CORSConfig(origins=["*"]),  # type: ignore[call-arg]
            rate_limiting=RateLimitConfig(  # type: ignore[call-arg]
                enabled=True, requests_per_minute=2, burst=2
            ),
        )
        app = create_fastapi_app(server_config)
        client = TestClient(app, raise_server_exceptions=False)
        headers = {"Authorization": f"Bearer {_make_token('user-a')}"}

        first = client.get("/info", headers=headers)
        second = client.get("/info", headers=headers)
        third = client.get("/info", headers=headers)

        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429

    def test_anonymous_requests_fall_back_to_per_ip_bucketing(self):
        """With auth disabled, user_id is never set — rate limiting must still work."""
        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(enabled=False, strategy="replace"),  # type: ignore[call-arg]
            rate_limiting=RateLimitConfig(  # type: ignore[call-arg]
                enabled=True, requests_per_minute=2, burst=2
            ),
        )
        app = create_fastapi_app(server_config)
        client = TestClient(app, raise_server_exceptions=False)

        first = client.get("/info")
        second = client.get("/info")
        third = client.get("/info")

        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429


class TestAuditLoggingStillWorks:
    def test_admin_prefixed_request_is_still_audited(self, caplog):
        """Audit logging must keep recording requests after the reorder."""
        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(enabled=False, strategy="replace"),  # type: ignore[call-arg]
            audit_log_enabled=True,
        )
        app = create_fastapi_app(server_config)
        client = TestClient(app, raise_server_exceptions=False)

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            response = client.get("/api/v1/admin/anything")

        audit_records = [r for r in caplog.records if r.name == "orb.audit"]
        assert len(audit_records) == 1
        record = audit_records[0]
        assert record.method == "GET"
        assert record.path == "/api/v1/admin/anything"
        assert record.status_code == response.status_code
